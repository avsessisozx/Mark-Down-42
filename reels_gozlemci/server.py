"""Yerel arayüz sunucusu (yalnız 127.0.0.1). Arayüz bu sunucuyla JSON üzerinden konuşur."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from . import __version__, export, report
from .engine import Engine
from .paths import app_data_dir, clean_settings, default_export_dir, load_settings, save_settings
from .store import Store

APP_ID = "reels-gozlemci"


def ui_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "reels_gozlemci" / "ui"
    return Path(__file__).resolve().parent / "ui"


class AppState:
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else app_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir / "reels.sqlite3")
        self.engine = Engine(self.store, self.data_dir)
        self.settings: Dict[str, Any] = load_settings()
        self.token = secrets.token_urlsafe(16)
        self.last_poll = 0.0
        self.port = 0
        self.last_export = ""

    def out_dir(self) -> Path:
        d = self.settings.get("out_dir") or ""
        return Path(d) if d else default_export_dir()


def _open_folder(path: Path) -> None:
    if os.environ.get("REELS_GOZLEMCI_NO_OPEN"):
        return
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def _copy_to_clipboard(text: str) -> bool:
    if sys.platform != "win32" or os.environ.get("REELS_GOZLEMCI_NO_OPEN"):
        return False
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                       input=text.encode("utf-8"), timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:
        return False


def make_handler(state: AppState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ReelsGozlemci"

        def log_message(self, *args: Any) -> None:
            return

        # --------------------------------------------------------------- yardımcılar
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self) -> Dict[str, Any]:
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(min(n, 1_000_000)) if n else b""
                data = json.loads(raw.decode("utf-8")) if raw else {}
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        # --------------------------------------------------------------- GET
        def do_GET(self) -> None:
            if not self._host_ok():
                return self._json({"error": "host"}, 403)
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                html = (ui_dir() / "index.html").read_text(encoding="utf-8")
                html = html.replace("__TOKEN__", state.token).replace("__VERSION__", __version__)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path == "/api/ping":
                return self._json({"app": APP_ID, "version": __version__})
            if path == "/api/state":
                state.last_poll = time.monotonic()
                snap = state.engine.snapshot()
                snap["settings"] = state.settings
                snap["version"] = __version__
                snap["views_total"] = state.store.views_total()
                snap["last_export"] = state.last_export
                return self._json(snap)
            if path == "/api/report":
                snap = state.engine.snapshot()
                return self._json({"text": report.build(state.settings, snap)})
            self._json({"error": "not_found"}, 404)

        # --------------------------------------------------------------- POST
        def do_POST(self) -> None:
            if not self._host_ok():
                return self._json({"error": "host"}, 403)
            if self.headers.get("X-Token") != state.token:
                return self._json({"error": "token"}, 403)
            path = urlsplit(self.path).path
            data = self._body()
            eng = state.engine

            if path == "/api/settings":
                state.settings = save_settings({**state.settings, **data})
                if eng.running:
                    eng.update_settings({k: state.settings[k] for k in
                                         ("scenario", "min_s", "max_s", "full_cap_s", "auto", "limit")})
                return self._json({"ok": True, "settings": state.settings})
            if path == "/api/start":
                if data:
                    state.settings = save_settings({**state.settings, **data})
                ok = eng.start(state.settings)
                return self._json({"ok": ok, "error": None if ok else "already_running"})
            if path == "/api/pause":
                eng.command("pause")
                return self._json({"ok": True})
            if path == "/api/resume":
                eng.command("resume")
                return self._json({"ok": True})
            if path == "/api/stop":
                eng.stop()
                return self._json({"ok": True})
            if path == "/api/star":
                eng.command("star")
                return self._json({"ok": True})
            if path == "/api/note":
                eng.command("note", text=str(data.get("text") or ""))
                return self._json({"ok": True})
            if path == "/api/export":
                try:
                    folder = export.export_all(state.store, state.out_dir())
                except OSError as exc:
                    return self._json({"ok": False, "error": str(exc)}, 500)
                state.last_export = str(folder)
                eng.log("ok", f"Dışa aktarıldı: {folder}")
                _open_folder(folder)
                return self._json({"ok": True, "folder": str(folder)})
            if path == "/api/open-folder":
                target = Path(state.last_export) if state.last_export else state.out_dir()
                target.mkdir(parents=True, exist_ok=True)
                _open_folder(target)
                return self._json({"ok": True, "folder": str(target)})
            if path == "/api/report-copy":
                text = report.build(state.settings, eng.snapshot())
                return self._json({"ok": _copy_to_clipboard(text), "text": text})
            self._json({"error": "not_found"}, 404)

    return Handler


def start_server(state: AppState, port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    state.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    return server
