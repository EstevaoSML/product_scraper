"""One browser process per leased session; only this worker owns session state."""
import json
import os
import queue
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time

from fastapi import HTTPException
from app.navigation import MAX_NAVIGATION_BYTES


class ProcessSession:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='navigation-')
        self.process = None
        self.answers = queue.Queue(maxsize=1)
        try:
            env = {k: v for k, v in os.environ.items() if k not in ('API_KEY', 'API_KEY_FILE', 'SCRAPER_BACKEND_KEY')}
            self.process = subprocess.Popen([sys.executable, '-m', 'app.navigation_browser'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                start_new_session=True, env={**env, 'BROWSER_JOB_DIR': self.directory.name})
            threading.Thread(target=self.read_answers, daemon=True).start()
        except Exception:
            self.close()
            raise

    def read_answers(self):
        while True:
            line = self.process.stdout.readline(MAX_NAVIGATION_BYTES + 1)
            if not line:
                try:
                    self.answers.put_nowait(b'')
                except queue.Full:
                    pass
                return
            try:
                self.answers.put_nowait(line)
            except queue.Full:
                return
            if len(line) > MAX_NAVIGATION_BYTES:
                return

    def call(self, action, arguments, timeout):
        self.process.stdin.write(json.dumps({'action': action, 'arguments': arguments}).encode() + b'\n')
        self.process.stdin.flush()
        line = self.answers.get(timeout=timeout)
        if not line or len(line) > MAX_NAVIGATION_BYTES:
            raise ValueError('Invalid browser protocol')
        result = json.loads(line)
        if not isinstance(result, dict):
            raise ValueError('Invalid browser protocol')
        return result

    def close(self):
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=5)
            for pipe in (self.process.stdin, self.process.stdout):
                pipe.close()
        self.directory.cleanup()


class SessionManager:
    def __init__(self, slot, factory=ProcessSession, clock=time.monotonic):
        self.slot, self.factory, self.clock = slot, factory, clock
        self.lock = threading.Lock()
        self.active = None
        self.stop = threading.Event()

    def start(self):
        self.stop.clear()
        self.thread = threading.Thread(target=self.reap, daemon=True)
        self.thread.start()

    def reap(self):
        while not self.stop.wait(1):
            with self.lock:
                if self.active and self.clock() >= self.active['deadline']:
                    self.dispose()

    def shutdown(self):
        self.stop.set()
        with self.lock:
            self.dispose()
        if hasattr(self, 'thread'):
            self.thread.join(timeout=2)

    def dispose(self):
        if self.active:
            active, self.active = self.active, None
            try:
                active['process'].close()
            finally:
                self.slot.release()

    def execute(self, action, arguments, owner):
        if not self.lock.acquire(blocking=False):
            raise HTTPException(429, 'Browser busy', headers={'Retry-After': '3'})
        try:
            if self.active and self.clock() >= self.active['deadline']:
                self.dispose()
            if action == 'open_page':
                if not self.slot.acquire(blocking=False):
                    raise HTTPException(429, 'Browser busy', headers={'Retry-After': '3'})
                try:
                    process = self.factory()
                except Exception:
                    self.slot.release()
                    raise HTTPException(503, 'Browser unavailable') from None
                self.active = {'id': secrets.token_urlsafe(32), 'owner': owner, 'process': process,
                               'deadline': self.clock()+180, 'actions': 0}
            active = self.active
            if not active or active['owner'] != owner or (action != 'open_page' and active['id'] != arguments.get('session_id')):
                raise HTTPException(404, 'Session unavailable; open a new page')
            if action == 'close_session':
                self.dispose()
                return {'session_id': arguments['session_id'], 'closed': True}
            if active['actions'] >= 10:
                self.dispose()
                raise HTTPException(409, 'Session action budget exhausted; session closed')
            active['actions'] += 1
            try:
                result = active['process'].call(action, arguments, timeout=min(60, max(0.1, active['deadline']-self.clock())))
            except (OSError, ValueError, queue.Empty):
                self.dispose()
                raise HTTPException(502, 'Browser failed; session closed') from None
            if 'error' in result:
                code = result['error']
                recoverable = code in ('stale_snapshot', 'unknown_element', 'action_not_allowed', 'image_unavailable') and action != 'open_page'
                if not recoverable:
                    self.dispose()
                statuses = {
                    'destination_rejected': 400, 'outside_retailer': 400,
                    'browser_timeout': 504, 'browser_error': 502,
                    'stale_snapshot': 409, 'unknown_element': 409,
                    'action_not_allowed': 409, 'page_limit': 409,
                    'image_unavailable': 409,
                }
                messages = {
                    'destination_rejected': 'Destination or redirect was rejected by the public URL policy; session closed',
                    'outside_retailer': 'Navigation left the selected retailer domain; session closed',
                    'browser_timeout': 'Retailer page did not finish loading before the browser timeout; session closed',
                    'browser_error': 'Browser failed while opening or inspecting the page; session closed',
                    'stale_snapshot': 'Element reference is stale; inspect the page again',
                    'unknown_element': 'Element was not present in the current snapshot; inspect the page again',
                    'action_not_allowed': 'The requested page action is not allowed',
                    'page_limit': 'Session page limit reached; session closed',
                    'image_unavailable': 'The selected image could not be captured; inspect the page again',
                }
                raise HTTPException(statuses.get(code, 502),
                                    {'code': code, 'message': messages.get(code, 'Navigation failed; session closed')})
            return {**result, 'session_id': active['id'], 'actions_remaining': 10-active['actions'],
                    'expires_in_seconds': max(0, int(active['deadline']-self.clock()))}
        finally:
            self.lock.release()
