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
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from . import __version__, export, hesap_verisi, oranlar, profil, report, session, winbrowsers
from .engine import Engine
from .livewrite import LiveWriter
from .paths import (app_data_dir, check_writable, clean_settings, default_export_dir, fallback_export_dir,
                    load_settings, save_settings)
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
        self.settings: Dict[str, Any] = load_settings()
        self.live = LiveWriter(self.store, self._live_dir, lambda level, text: self.engine.log(level, text))
        self.engine = Engine(self.store, self.data_dir, self.live)
        self.token = secrets.token_urlsafe(16)
        self.last_poll = 0.0
        self.port = 0
        self.last_export = ""
        self.ext_token = ""
        self._last_live_warning = ""
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

    def resolve_out_dir(self) -> tuple:
        """(klasör, uyarı): seçilen klasör yazılabilirse o; değilse yedek klasör ve nedenini anlatan uyarı."""
        wanted = self.out_dir()
        err = check_writable(wanted)
        if err is None:
            return wanted, ""
        backup = fallback_export_dir()
        err2 = check_writable(backup)
        if err2 is not None:
            return wanted, f"Klasöre yazılamadı ({wanted}): {err}. Yedek klasör de açılamadı: {err2}"
        return backup, (f"Seçili klasöre yazılamadı ({wanted}): {err}. Veriler yedek klasöre yazıldı: {backup}")

    def _live_dir(self) -> Optional[Path]:
        """Canlı yazma klasörü: ayar kapalıysa None; seçili klasör yazılamıyorsa yedek klasör (bir kez uyarılır)."""
        if not self.settings.get("live_write"):
            return None
        folder, warning = self.resolve_out_dir()
        if warning and warning != self._last_live_warning:
            self._last_live_warning = warning
            self.engine.log("warn", warning)
        if check_writable(folder) is not None:
            return None
        return folder


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


def pick_folder(initial: str = "", title: str = "Verilerin kaydedileceği klasörü seç") -> Optional[str]:
    """Windows'un klasör seçme penceresini açar; seçilen yolu ya da (iptal/başarısız) None döndürür."""
    if sys.platform != "win32" or os.environ.get("REELS_GOZLEMCI_NO_OPEN"):
        return os.environ.get("REELS_GOZLEMCI_TEST_PICK") or None
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$f = New-Object System.Windows.Forms.FolderBrowserDialog
$f.Description = $env:RG_TITLE
$f.ShowNewFolderButton = $true
if ($env:RG_INITIAL -and (Test-Path -LiteralPath $env:RG_INITIAL)) { $f.SelectedPath = $env:RG_INITIAL }
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.StartPosition = 'CenterScreen'
if ($f.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }
"""
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                             env=dict(os.environ, RG_INITIAL=initial, RG_TITLE=title), capture_output=True, timeout=600,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = res.stdout.decode("utf-8", "replace").strip()
        return out or None
    except Exception:
        return None


def pick_file(title: str = "Instagram'dan indirdiğin dosyayı seç") -> Optional[str]:
    """Windows'un dosya seçme penceresini açar (ZIP ya da JSON); iptal/başarısızsa None."""
    if sys.platform != "win32" or os.environ.get("REELS_GOZLEMCI_NO_OPEN"):
        return os.environ.get("REELS_GOZLEMCI_TEST_PICK") or None
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$f = New-Object System.Windows.Forms.OpenFileDialog
$f.Title = $env:RG_TITLE
$f.Filter = 'Instagram verisi (*.zip;*.json)|*.zip;*.json|Tüm dosyalar (*.*)|*.*'
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.StartPosition = 'CenterScreen'
if ($f.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.FileName }
"""
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                             env=dict(os.environ, RG_TITLE=title), capture_output=True, timeout=600,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = res.stdout.decode("utf-8", "replace").strip()
        return out or None
    except Exception:
        return None


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

        def _analysis(self, query: str) -> Dict[str, Any]:
            """Analiz penceresi ve Veriler sekmesi için: izleme kayıtları (Reel bilgisiyle birleşik) + oturumlar."""
            from urllib.parse import parse_qs
            try:
                limit = int(parse_qs(query).get("limit", ["0"])[0])
            except ValueError:
                limit = 0
            rows = state.store.export_views()
            if limit > 0:
                rows = rows[-limit:]
            slim = []
            for r in rows:
                r = dict(r)
                r["caption"] = (r.get("caption") or "")[:400]
                for k in ("t_end", "has_liked", "taken_at", "loops"):
                    r.pop(k, None)
                slim.append(r)
            return {"views": slim, "sessions": state.store.sessions(), "total": state.store.views_total()}

        def _report_snapshot(self) -> Dict[str, Any]:
            snap = state.engine.snapshot()
            snap["numeric_fields"] = state.store.numeric_fields_summary()
            snap["account_data"] = state.store.account_summary()
            folder, warning = state.resolve_out_dir()
            snap["out_dir"] = f"{folder}" + (f" (UYARI: {warning})" if warning else "")
            return snap

        # --------------------------------------------------------------- GET
        def do_GET(self) -> None:
            if not self._host_ok():
                return self._json({"error": "host"}, 403)
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                html = (ui_dir() / "index.html").read_text(encoding="utf-8")
                html = html.replace("__TOKEN__", state.token).replace("__VERSION__", __version__)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path in ("/analiz", "/analiz.html"):
                html = (ui_dir() / "analiz.html").read_text(encoding="utf-8")
                html = html.replace("__TOKEN__", state.token).replace("__VERSION__", __version__)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path in ("/profil", "/profil.html"):
                html = (ui_dir() / "profil.html").read_text(encoding="utf-8")
                html = html.replace("__TOKEN__", state.token).replace("__VERSION__", __version__)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path == "/api/analysis":
                return self._json(self._analysis(urlsplit(self.path).query))
            if path == "/api/profile":
                return self._json(profil.build_profile(state.store))
            if path == "/api/ratios":
                return self._json(oranlar.analyze(state.store.export_reels()))
            if path == "/api/watchlist":
                return self._json({"items": state.store.watch_overview(), "rechecking": state.engine.rechecking,
                                   "running": state.engine.running})
            if path == "/api/ping":
                return self._json({"app": APP_ID, "version": __version__})
            if path == "/api/state":
                state.last_poll = time.monotonic()
                snap = state.engine.snapshot()
                snap["settings"] = state.settings
                snap["version"] = __version__
                snap["views_total"] = state.store.views_total()
                snap["last_export"] = state.last_export
                snap["default_out_dir"] = str(default_export_dir())
                snap["account"] = {"pending": bool(state.pending_cookies), "waiting": state.chrome_waiting()}
                return self._json(snap)
            if path == "/api/report":
                return self._json({"text": report.build(state.settings, self._report_snapshot())})
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
                base, warning = state.resolve_out_dir()
                try:
                    folder = export.export_all(state.store, base)
                except OSError as exc:
                    msg = f"{type(exc).__name__}: {exc}"
                    eng.log("error", f"Dışa aktarılamadı ({base}): {msg}")
                    return self._json({"ok": False, "error": f"Dışa aktarılamadı ({base}): {msg}"}, 500)
                state.last_export = str(folder)
                if warning:
                    eng.log("warn", warning)
                eng.log("ok", f"Dışa aktarıldı: {folder}")
                _open_folder(folder)
                return self._json({"ok": True, "folder": str(folder), "warning": warning})
            if path == "/api/open-folder":
                target, warning = state.resolve_out_dir()
                if state.last_export and Path(state.last_export).is_dir():
                    target = Path(state.last_export)
                _open_folder(target)
                return self._json({"ok": True, "folder": str(target), "warning": warning})
            if path == "/api/folder-test":
                wanted = state.out_dir()
                err = check_writable(wanted)
                if err is None:
                    eng.log("ok", f"Klasör denendi, yazılabiliyor: {wanted}")
                    return self._json({"ok": True, "folder": str(wanted)})
                eng.log("error", f"Klasöre yazılamadı ({wanted}): {err}")
                return self._json({"ok": False, "folder": str(wanted), "error": err})
            if path == "/api/pick-folder":
                chosen = pick_folder(str(state.out_dir()))
                if not chosen:
                    return self._json({"ok": False, "cancelled": True})
                err = check_writable(Path(chosen))
                state.settings = save_settings({**state.settings, "out_dir": chosen})
                if err:
                    eng.log("error", f"Seçilen klasöre yazılamıyor ({chosen}): {err}")
                else:
                    eng.log("ok", f"Veri klasörü seçildi: {chosen}")
                return self._json({"ok": True, "folder": chosen, "writable": err is None, "error": err or "",
                                   "settings": state.settings})
            if path == "/api/account-import":
                src = str(data.get("path") or "").strip().strip('"')
                if not src and data.get("pick"):
                    src = (pick_folder("", "Açılmış Instagram veri klasörünü seç") if data.get("pick") == "folder"
                           else pick_file()) or ""
                    if not src:
                        return self._json({"ok": False, "cancelled": True})
                p = Path(src)
                if not p.exists():
                    return self._json({"ok": False, "error": f"Dosya ya da klasör bulunamadı: {src}"}, 400)
                try:
                    summary = hesap_verisi.import_package(p, state.store)
                except (OSError, ValueError, zipfile.BadZipFile) as exc:
                    eng.log("error", f"Hesap verisi okunamadı ({src}): {type(exc).__name__}: {exc}")
                    return self._json({"ok": False, "error": f"Okunamadı: {type(exc).__name__}: {exc}"}, 500)
                eng.log("ok", f"Hesap verisi içe aktarıldı: {summary['items']} kayıt, {summary['json_files']} JSON dosyası.")
                if summary["unrecognized"]:
                    eng.log("debug", "Tanınmayan dosyalar: " + ", ".join(summary["unrecognized"][:15]))
                return self._json({"ok": summary["items"] > 0, "summary": summary,
                                   "error": "" if summary["items"] else (summary["errors"][0] if summary["errors"]
                                                                          else "Tanınan veri bulunamadı.")})
            if path == "/api/account-clear":
                state.store.account_clear()
                return self._json({"ok": True})
            if path == "/api/watch-add":
                code = str(data.get("code") or eng.current_code or "").strip()
                if not code:
                    return self._json({"ok": False, "error": "Şu an bir Reel görünmüyor."}, 400)
                return self._json({"ok": True, "new": state.store.watch_add(code, "elle"), "code": code})
            if path == "/api/watch-remove":
                state.store.watch_remove(str(data.get("code") or ""))
                return self._json({"ok": True})
            if path == "/api/recheck":
                if not eng.running or not eng.logged_in:
                    return self._json({"ok": False, "error": "Kontrol için program çalışıyor ve Instagram'a girişli olmalı (Başlat)."}, 400)
                eng.command("recheck", limit=int(data.get("limit") or 0) or None)
                return self._json({"ok": True})
            if path == "/api/report-copy":
                text = report.build(state.settings, self._report_snapshot())
                return self._json({"ok": _copy_to_clipboard(text), "text": text})
            self._json({"error": "not_found"}, 404)

    return Handler


def start_server(state: AppState, port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    state.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    return server
