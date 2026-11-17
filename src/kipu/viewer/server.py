"""Local viewer: a stdlib HTTP server on 127.0.0.1 plus a polling file watcher.

The watcher (not an editor hook) is the primary trigger, so edits made by any agent, editor or
shell command show up. Each rescan bumps a version number; the page polls /api/version.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from kipu import scan as scanner
from kipu.files import snapshot_mtimes
from kipu.notes import NOTES_FILE

STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/style.css": ("style.css", "text/css")}


class State:
    def __init__(self, root: Path, out: Path):
        self.root, self.out = root, out
        self.lock = threading.Lock()
        self.version = 0
        self.payload = b"{}"
        self.error: str | None = None

    def rescan(self, reset: bool = False) -> None:
        try:
            data = scanner.run(self.root, self.out, reset_baseline=reset)
            payload = json.dumps(data, ensure_ascii=False).encode()
            err = None
        except Exception:  # keep serving the last good map
            payload, err = None, traceback.format_exc(limit=3)
        with self.lock:
            if payload is not None:
                self.payload = payload
            self.error = err
            self.version += 1


def _snapshot(state: State) -> dict[str, float]:
    snap = snapshot_mtimes(state.root)
    notes = state.out / NOTES_FILE            # explanations written by the agent also refresh the map
    if notes.exists():
        snap["\0notes"] = notes.stat().st_mtime
    return snap


def _watch(state: State, interval: float = 0.8) -> None:
    last = _snapshot(state)
    while True:
        time.sleep(interval)
        try:
            cur = _snapshot(state)
        except OSError:
            continue
        if cur != last:
            time.sleep(0.3)  # let multi-file edits settle
            last = _snapshot(state)
            state.rescan()


def _handler(state: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in STATIC:
                name, ctype = STATIC[path]
                body = resources.files("kipu.viewer").joinpath("static", name).read_bytes()
                return self._send(body, ctype)
            if path == "/api/version":
                with state.lock:
                    body = json.dumps({"version": state.version, "error": state.error}).encode()
                return self._send(body, "application/json")
            if path == "/api/graph":
                with state.lock:
                    body = state.payload
                return self._send(body, "application/json")
            self._send(b"not found", "text/plain", 404)

        def do_POST(self):
            if self.path == "/api/baseline":
                state.rescan(reset=True)
                return self._send(b'{"ok":true}', "application/json")
            self._send(b"not found", "text/plain", 404)

    return H


def serve(root: Path, out: Path | None, port: int, open_browser: bool = True) -> int:
    root = root.resolve()
    out = (out or scanner.default_out(root)).resolve()
    state = State(root, out)
    state.rescan()
    if state.error:
        print(state.error)
    threading.Thread(target=_watch, args=(state,), daemon=True).start()
    server = None
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), _handler(state))
            break
        except OSError:
            continue
    if server is None:
        print(f"kipu: no hay puertos libres entre {port} y {port + 19}")
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"kipu · {root.name} → {url}  (vigilando cambios; Ctrl+C para salir)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
