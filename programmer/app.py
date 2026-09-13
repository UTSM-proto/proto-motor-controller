"""Run with Python 3.10+ on Windows; opens a loopback-only browser GUI."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlparse
import webbrowser

from backend import Programmer, devices, toolchain
from config import FIELDS, defaults, validate


def make_server(port=0):
    programmer = Programmer()
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
                if self.path == "/api/validate": return self.respond(200, validate(data))
                if self.path == "/api/build":
                    if type(data.get("flash", False)) is not bool: raise ValueError("Invalid flash flag")
                    return self.respond(202, programmer.start(data["config"], data.get("flash", False), data.get("serial")))
                self.respond(404, {"error": "Not found"})
            except (ValueError, KeyError, TypeError) as exc: self.respond(400, {"error": str(exc)})
            except Exception as exc: self.respond(500, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server, f"http://127.0.0.1:{server.server_port}/#token={token}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server, url = make_server(args.port)
    print("UTSM Pico Programmer: " + url, flush=True)
    print("Keep this process running. Close with Ctrl+C.", flush=True)
    if not args.no_browser: threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
