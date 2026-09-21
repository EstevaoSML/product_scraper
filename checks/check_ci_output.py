"""Docker output must work on Windows even with accented bind-mount paths."""
import json
import subprocess
import sys

import pytest

from scripts import container_ci


def check_utf8_stdout_and_stderr_from_real_process():
    payload = json.dumps([{'Source': 'C:/Users/estev/OneDrive/Área de Trabalho'}], ensure_ascii=False)
    diagnostic = 'Inspeção concluída'
    code = (f'import sys; sys.stdout.buffer.write({payload.encode("utf-8")!r}); '
            f'sys.stderr.buffer.write({diagnostic.encode("utf-8")!r})')
    result = container_ci.run_command([sys.executable, '-c', code], capture_output=True, check=True, timeout=10)
    assert result.stdout == payload
    assert result.stderr == diagnostic
    assert json.loads(result.stdout)[0]['Source'].endswith('Área de Trabalho')


def check_inspection_decodes_utf8_without_locale_text_mode(monkeypatch):
    payload = [{'Source': 'C:/Área de Trabalho', 'Internal': True}]
    def docker(command, **kwargs):
        assert command == ['docker', 'network', 'inspect', 'browser']
        assert kwargs.get('text', False) is False
        assert kwargs['capture_output'] is True
        return subprocess.CompletedProcess(command, 0, json.dumps(payload, ensure_ascii=False).encode(), b'')
    monkeypatch.setattr(container_ci.subprocess, 'run', docker)
    assert container_ci.inspect_json('network', 'inspect', 'browser') == payload


@pytest.mark.parametrize('output', [None, b'', b'   '])
def check_missing_inspection_output_has_actionable_error(monkeypatch, output):
    monkeypatch.setattr(container_ci.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a[0], 0, output, b''))
    with pytest.raises(RuntimeError, match='no captured output'):
        container_ci.inspect_json('inspect', 'container-id')


@pytest.mark.parametrize('output', [b'not json', b'[]', b'null', b'{}', b'[1]'])
def check_malformed_inspection_fails_instead_of_passing(monkeypatch, output):
    monkeypatch.setattr(container_ci.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a[0], 0, output, b''))
    with pytest.raises(RuntimeError, match='Docker inspection'):
        container_ci.inspect_json('inspect', 'container-id')


def check_invalid_utf8_fails_in_main_thread():
    code = 'import sys; sys.stdout.buffer.write(bytes([255]))'
    with pytest.raises(RuntimeError, match='invalid UTF-8 on stdout'):
        container_ci.run_command([sys.executable, '-c', code], capture_output=True, check=True, timeout=10)


def check_failed_command_still_fails():
    with pytest.raises(subprocess.CalledProcessError):
        container_ci.run_command([sys.executable, '-c', 'raise SystemExit(7)'], capture_output=True, check=True, timeout=10)
