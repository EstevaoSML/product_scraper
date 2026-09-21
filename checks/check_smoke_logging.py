"""Exercise the real PowerShell client against a local stub HTTP API."""
import json
import re
import shutil
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize("structured", [True, False, "success"])
def check_powershell_error_storage(structured):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is not installed")
    root = Path(__file__).resolve().parents[1]
    runtime = root / "reports" / ("smoke-" + uuid.uuid4().hex)
    request_id = str(uuid.uuid4())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ready"}')

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            if structured == "success":
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"html": "<html>Olá</html>", "browser_version": "152.0"}).encode())
                return
            self.send_response(400)
            self.send_header("Content-Type", "application/json" if structured else "text/html")
            self.end_headers()
            self.wfile.write(json.dumps({"detail": "secret-response", "code": "host_not_allowed", "request_id": request_id}).encode() if structured else b"<html>secret-response</html>")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        (runtime / "scripts").mkdir(parents=True)
        (runtime / "secrets").mkdir()
        (runtime / "secrets/api_key.txt").write_text("secret-key-" * 4)
        shutil.copyfile(root / "scripts/smoke.ps1", runtime / "scripts/smoke.ps1")
        command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runtime / "scripts/smoke.ps1"),
                   "-BaseUrl", f"http://127.0.0.1:{server.server_port}", "-Url", "https://example.com/?token=secret-query"]
        if structured == "success":
            (runtime / "outputs").mkdir()
            previous = runtime / "outputs/scrape.json"
            previous.write_text('{"previous":true}')
            for _ in range(2):
                result = subprocess.run(command, capture_output=True, timeout=30)
                assert result.returncode == 0, result.stderr
            files = list((runtime / "outputs").glob("scrape_*.json"))
            assert len(files) == 2
            for path in files:
                assert re.fullmatch(r"scrape_\d{8}_\d{6}_\d{3}Z_[a-f0-9]{32}\.json", path.name)
                assert json.loads(path.read_text(encoding="utf-8"))["html"] == "<html>Olá</html>"
            assert previous.read_text() == '{"previous":true}'
            assert not (runtime / "logs").exists()
            return
        result = subprocess.run(command, capture_output=True, timeout=30)
        assert result.returncode != 0
        text = (runtime / "logs/client-errors.jsonl").read_text(encoding="utf-8-sig")
        row = json.loads(text)
        assert row["code"] == ("host_not_allowed" if structured else "client_error")
        assert row["request_id"] == (request_id if structured else None)
        assert "secret" not in text
        assert b"secret-response" not in result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        # This invocation's disposable fixture only, within reports.
        assert runtime.parent == root / "reports" and runtime.name.startswith("smoke-")
        shutil.rmtree(runtime)
