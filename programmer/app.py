"""Run with Python 3.10+ on Windows; opens a loopback-only browser GUI."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError
import webbrowser

from backend import Programmer, devices, toolchain
from config import FIELDS, defaults, validate
from serial_monitor import SerialMonitor


def service_signature():
    digest = hashlib.sha256()
    for name in ('app.py', 'backend.py', 'build_cache.py', 'config.py', 'usb_transport.py', 'windows_devices.ps1', 'serial_monitor.py', 'serial_reader.ps1'):
        digest.update((Path(__file__).parent / name).read_bytes())
    return digest.hexdigest()


LOADED_SIGNATURE = service_signature()


def existing_server(port):
    record = Path(__file__).resolve().parents[1] / '.programmer/server.json'
    try:
        url = json.loads(record.read_text())['url']
        parsed = urlparse(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.port != port:
            return None
        token = parsed.fragment.removeprefix('token=')
        base = f'http://127.0.0.1:{port}'
        headers = {'X-Programmer-Token': token}
        with urlopen(Request(base + '/api/server', headers=headers), timeout=3) as response:
            info = json.load(response)
    except (OSError, ValueError, KeyError, URLError):
        return None
    if info['version'] == LOADED_SIGNATURE:
        return url
    # Graceful shutdown is rejected while a build or USB write is active.
    with urlopen(Request(base + '/api/shutdown', data=b'{}', headers=headers), timeout=3):
        pass
    time.sleep(1)
    return None


def make_server(port=0):
    monitor = SerialMonitor(Path(__file__).resolve().parents[1] / '.programmer')
    programmer = Programmer(monitor)
    io_gate = threading.RLock()
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def respond(self, code, data, kind="application/json"):
            body = json.dumps(data).encode() if kind == "application/json" else data
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            expected = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected: return False
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + expected: return False
            return secrets.compare_digest(self.headers.get("X-Programmer-Token", ""), token)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/app.js", "/style.css"):
                if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    return self.respond(403, {"error": "Invalid host"})
                name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path]
                kind = {"/": "text/html; charset=utf-8", "/app.js": "text/javascript; charset=utf-8", "/style.css": "text/css; charset=utf-8"}[path]
                return self.respond(200, (Path(__file__).parent / "web" / name).read_bytes(), kind)
            if not self.authorized(): return self.respond(403, {"error": "Reopen the app using Launch Programmer.cmd."})
            try:
                if path == "/api/config": return self.respond(200, dict(fields=FIELDS, defaults=defaults(), tools=toolchain()))
                if path == "/api/devices": return self.respond(200, devices())
                if path == "/api/job": return self.respond(200, programmer.status())
                if path == '/api/serial': return self.respond(200, monitor.snapshot())
                if path == "/api/server": return self.respond(200, dict(version=LOADED_SIGNATURE))
                if path == "/api/artifact":
                    job = programmer.status()
                    if not job or not job.get("artifact"): raise ValueError("No artifact available.")
                    return self.respond(200, Path(job["artifact"]).read_bytes(), "application/octet-stream")
                self.respond(404, {"error": "Not found"})
            except Exception as exc: self.respond(400, {"error": str(exc)})

        def do_POST(self):
            if not self.authorized(): return self.respond(403, {"error": "Request rejected"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768: raise ValueError("Invalid request size")
                data = json.loads(self.rfile.read(length))
                if self.path in ('/api/serial/start', '/api/serial/stop', '/api/serial/clear'):
                    with io_gate:
                        if self.path == '/api/serial/start':
                            job = programmer.status()
                            if job and job['status'] == 'running' and job.get('flash'):
                                raise ValueError('Wait for programming to finish before opening the serial monitor.')
                            selected = next((d for d in devices() if d['serial'] == data.get('serial')), None)
                            if not selected: raise ValueError('Select a connected Pico.')
                            monitor.start(selected)
                        elif self.path == '/api/serial/stop': monitor.stop()
                        else: monitor.clear()
                    return self.respond(200, monitor.snapshot())
                if self.path == '/api/shutdown':
                    job = programmer.status()
                    if job and job['status'] == 'running':
                        raise ValueError('Wait for the active programming operation before restarting the dashboard.')
                    monitor.stop()
                    self.respond(200, {'status': 'stopping'})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if self.path == "/api/validate": return self.respond(200, validate(data))
                if self.path == "/api/build":
                    if service_signature() != LOADED_SIGNATURE:
                        raise ValueError('The programmer has been updated. Run Launch Programmer.cmd to restart it before programming.')
                    if type(data.get("flash", False)) is not bool: raise ValueError("Invalid flash flag")
                    with io_gate:
                        job = programmer.start(data["config"], data.get("flash", False), data.get("serial"))
                    return self.respond(202, job)
                self.respond(404, {"error": "Not found"})
            except (ValueError, KeyError, TypeError) as exc: self.respond(400, {"error": str(exc)})
            except Exception as exc: self.respond(500, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    close = server.server_close
    def close_with_monitor():
        monitor.stop()
        close()
    server.server_close = close_with_monitor
    return server, f"http://127.0.0.1:{server.server_port}/#token={token}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    url = existing_server(args.port)
    if url:
        print('Programmer already running: ' + url, flush=True)
        if not args.no_browser: webbrowser.open(url)
        raise SystemExit(0)
    server, url = make_server(args.port)
    record = Path(__file__).resolve().parents[1] / '.programmer/server.json'
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(dict(url=url)), encoding='utf-8')
    print("UTSM Pico Programmer: " + url, flush=True)
    print("Keep this process running. Close with Ctrl+C.", flush=True)
    if not args.no_browser: threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
