"""Yerel arayüz sunucusu (yalnız 127.0.0.1). Arayüz bu sunucuyla JSON üzerinden konuşur."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from . import __version__, export, report, session, winbrowsers
from .engine import Engine
from .paths import app_data_dir, clean_settings, default_export_dir, load_settings, save_settings
from .store import Store

APP_ID = "reels-gozlemci"
EXTENSION_FOLDER = "chrome_eklenti"
EXTENSION_TTL = 10 * 60


def ui_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "reels_gozlemci" / "ui"
    return Path(__file__).resolve().parent / "ui"


def extension_source() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) / "reels_gozlemci" if base else Path(__file__).resolve().parent
    return root / EXTENSION_FOLDER


def install_extension_folder(data_dir: Path) -> Optional[Path]:
    """Eklenti dosyalarını kalıcı bir klasöre kopyalar (Chrome 'paketlenmemiş öğe' olarak bu klasörü yükler)."""
    src, dst = extension_source(), Path(data_dir) / "chrome-eklenti"
    try:
        if not (src / "manifest.json").exists():
            return None
        shutil.copytree(src, dst, dirs_exist_ok=True)
    except OSError:
        return None
    return dst


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
        self.ext_token = ""
        self.ext_expires = 0.0
        self.pending_cookies: Dict[str, str] = {}      # Chrome'dan gelen, henüz tarayıcıya yerleştirilmemiş hesap

    def chrome_waiting(self) -> bool:
        return bool(self.ext_token) and time.monotonic() < self.ext_expires

    def add_account(self, cookies: Dict[str, str]) -> None:
        """Hesabı çalışan motora verir; motor çalışmıyorsa bir sonraki Başlat'ta yerleştirilir."""
        if self.engine.running:
            self.engine.command("import_cookies", cookies=cookies)
        else:
            self.pending_cookies = dict(cookies)
        self.engine.log("ok", "Hesap bilgisi alındı (" + ", ".join(sorted(cookies)) + ").")

    def remove_account(self) -> None:
        self.pending_cookies = {}
        if self.engine.running:
            self.engine.command("logout")
            return
        for d in self.data_dir.glob("tarayici-*"):
            if d.is_dir() and d.name != "tarayici-bileseni":
                shutil.rmtree(d, ignore_errors=True)
        self.engine.log("info", "Hesap kaldırıldı (tarayıcı profili silindi).")

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


def _open_in_default_browser(url: str) -> bool:
    if os.environ.get("REELS_GOZLEMCI_NO_OPEN"):
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def _copy_to_clipboard(text: str) -> bool:
    """Metni panoya koyar. Türkçe karakterler bozulmasın diye UTF-8 dosya üzerinden gider (stdin değil)."""
    if sys.platform != "win32" or os.environ.get("REELS_GOZLEMCI_NO_OPEN"):
        return False
    import tempfile
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="rg-pano-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        env = dict(os.environ, RG_CLIP_FILE=tmp)
        cmd = "Set-Clipboard -Value ([IO.File]::ReadAllText($env:RG_CLIP_FILE, [Text.Encoding]::UTF8))"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], env=env, timeout=20,
                             capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return res.returncode == 0
    except Exception:
        return False
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def make_handler(state: AppState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ReelsGozlemci"

        def log_message(self, *args: Any) -> None:
            return

        # --------------------------------------------------------------- yardımcılar
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        _cors = ""

        def _extension_origin(self) -> str:
            origin = self.headers.get("Origin") or ""
            return origin if origin.startswith("chrome-extension://") else ""

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            if self._cors:
                self.send_header("Access-Control-Allow-Origin", self._cors)
                self.send_header("Vary", "Origin")
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
                snap["account"] = {"pending": bool(state.pending_cookies), "waiting": state.chrome_waiting()}
                return self._json(snap)
            if path == "/api/report":
                snap = state.engine.snapshot()
                return self._json({"text": report.build(state.settings, snap)})
            self._json({"error": "not_found"}, 404)

        def do_OPTIONS(self) -> None:
            ext = self._extension_origin()
            if not self._host_ok() or not ext or urlsplit(self.path).path != "/api/extension-session":
                return self._send(403, b"forbidden", "text/plain")
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", ext)
            self.send_header("Access-Control-Allow-Methods", "POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        # --------------------------------------------------------------- POST
        def do_POST(self) -> None:
            self._cors = ""
            if not self._host_ok():
                return self._json({"error": "host"}, 403)
            path = urlsplit(self.path).path
            if path == "/api/extension-session":
                # Chrome eklentisinden: oturum belirteci yerine tek kullanımlık kod + eklenti kökeni aranır.
                ext = self._extension_origin()
                if not ext:
                    return self._json({"error": "origin"}, 403)
                self._cors = ext
                data = self._body()
                token = str(data.get("token") or "")
                if not (state.chrome_waiting() and hmac.compare_digest(token, state.ext_token)):
                    return self._json({"error": "Kod geçersiz ya da süresi dolmuş. Programda 'Hesap ekle'ye tekrar bas."}, 403)
                state.ext_token = ""                      # tek kullanımlık
                cookies = session.clean_cookies(data.get("cookies"))
                if not cookies.get("sessionid"):
                    return self._json({"error": "Chrome'da Instagram'a giriş yapılmamış."}, 400)
                state.add_account(cookies)
                return self._json({"ok": True})
            if self.headers.get("X-Token") != state.token:
                return self._json({"error": "token"}, 403)
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
                ok = eng.start(state.settings, state.pending_cookies)
                if ok:
                    state.pending_cookies = {}
                return self._json({"ok": ok, "error": None if ok else "already_running"})
            if path == "/api/login-chrome":
                state.ext_token = secrets.token_urlsafe(12)
                state.ext_expires = time.monotonic() + EXTENSION_TTL
                url = f"https://www.instagram.com/#ii={state.port}.{state.ext_token}"
                eng.log("info", "Chrome'da Instagram açılıyor; eklenti oturumu gönderecek.")
                return self._json({"ok": True, "opened": _open_in_default_browser(url)})
            if path == "/api/login-cookie":
                cookies = session.parse_session_input(str(data.get("cookie") or ""))
                if not cookies.get("sessionid"):
                    return self._json({"ok": False, "error": "Kod anlaşılamadı. Chrome'daki sessionid değerini (ya da tüm Cookie satırını) yapıştır."}, 400)
                state.add_account(cookies)
                return self._json({"ok": True})
            if path == "/api/extension-open":
                folder = install_extension_folder(state.data_dir)
                if folder is None:
                    return self._json({"ok": False, "error": "Eklenti klasörü hazırlanamadı."}, 500)
                _open_folder(folder)
                return self._json({"ok": True, "path": str(folder)})
            if path == "/api/logout":
                state.remove_account()
                return self._json({"ok": True})
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
