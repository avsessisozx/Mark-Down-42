"""Programın giriş noktası: sunucuyu başlatır ve kenar arayüz penceresini açar."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import __version__, winbrowsers
from .paths import app_data_dir, screen_size
from .server import APP_ID, AppState, start_server

HEARTBEAT_TIMEOUT = 45
WINDOW_WAIT = 20
UI_WIDTH = 410


def say(text: str) -> None:
    try:
        print(text, flush=True)
    except Exception:
        pass


def _instance_file() -> Path:
    return app_data_dir() / "calisan.json"


def _existing_instance() -> Optional[int]:
    try:
        port = int(json.loads(_instance_file().read_text(encoding="utf-8"))["port"])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as resp:
            if json.loads(resp.read()).get("app") == APP_ID:
                return port
    except Exception:
        return None
    return None


def _app_window(exe: str, url: str, name: str) -> Optional[subprocess.Popen]:
    """Ekranın sağında dar bir pencere (Instagram penceresi solda açılır)."""
    sw, sh = screen_size()
    profile = app_data_dir() / f"arayuz-penceresi-{name}"
    cmd = [exe, f"--app={url}", f"--user-data-dir={profile}", f"--window-size={UI_WIDTH},{max(600, sh - 60)}",
           f"--window-position={max(0, sw - UI_WIDTH)},0", "--no-first-run", "--no-default-browser-check",
           "--disable-features=Translate"]
    if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
        cmd.append("--no-sandbox")
    try:
        return subprocess.Popen(cmd)
    except OSError:
        return None


def _message_box(text: str) -> None:
    if sys.platform != "win32":
        say(text)
        return

    def show() -> None:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, "Reels Gözlemci", 0x40 | 0x40000)
        except Exception:
            pass

    threading.Thread(target=show, daemon=True).start()


def window_launchers(url: str) -> List[Tuple[str, Callable[[], Tuple[bool, Optional[subprocess.Popen]]]]]:
    def with_exe(finder: Callable[[], Optional[str]], name: str):
        def launch():
            exe = finder()
            if not exe:
                return False, None
            proc = _app_window(exe, url, name)
            return proc is not None, proc
        return launch

    def default_browser():
        if os.environ.get("REELS_GOZLEMCI_FORCE_BUNDLED"):
            return False, None
        try:
            return bool(webbrowser.open(url)), None
        except Exception:
            return False, None

    return [
        ("edge", with_exe(winbrowsers.healthy_edge, "edge")),
        ("chrome", with_exe(winbrowsers.healthy_chrome, "chrome")),
        ("chromium", with_exe(winbrowsers.bundled_chromium_exe, "chromium")),
        ("default", default_browser),
    ]


def self_test() -> str:
    """Derlenmiş programın çalıştığını doğrular (GitHub derlemesinde kullanılır)."""
    import tempfile

    state = AppState(Path(tempfile.mkdtemp(prefix="rg-selftest-")))
    server = start_server(state)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{state.port}/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        assert "Reels Gözlemci" in html and state.token in html, "UI did not load"
        with urllib.request.urlopen(f"http://127.0.0.1:{state.port}/api/state", timeout=5) as resp:
            snap = json.loads(resp.read().decode("utf-8"))
        assert snap.get("phase") == "hazir", "state not ready"
        from . import extract
        sample = '{"data":{"a":{"edges":[{"node":{"media":{"code":"Abc123","pk":"1","like_count":5,"video_duration":3.5}}}]}}}'
        assert extract.extract_media(sample)[0]["code"] == "Abc123", "extractor broken"
    finally:
        server.shutdown()
        state.store.close()
    say("SELF-TEST OK: ui + state + extractor")
    return "ui"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ReelsGozlemci")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    winbrowsers.suppress_windows_error_dialogs()
    winbrowsers.prepare_playwright_env()

    if args.self_test:
        out = Path(os.environ.get("REELS_GOZLEMCI_SELFTEST_OUT", "selftest.txt"))
        try:
            out.write_text(f"OK {self_test()}", encoding="utf-8")
            return 0
        except BaseException:
            import traceback
            out.write_text("FAIL\n" + traceback.format_exc(), encoding="utf-8")
            return 1

    existing = _existing_instance()
    if existing and not args.no_window:
        for _n, launch in window_launchers(f"http://127.0.0.1:{existing}/")[:4]:
            ok, _p = launch()
            if ok:
                break
        return 0

    state = AppState()
    server = start_server(state, args.port)
    url = f"http://127.0.0.1:{state.port}/"
    try:
        _instance_file().write_text(json.dumps({"port": state.port, "pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass
    say(f"Reels Gozlemci: {url}")

    launchers = [] if args.no_window else window_launchers(url)
    window: Optional[subprocess.Popen] = None
    launched_at = 0.0
    ui_seen = False
    watch_process = False

    def next_window() -> bool:
        nonlocal window, launched_at
        while launchers:
            _name, launch = launchers.pop(0)
            ok, proc = launch()
            if ok:
                window, launched_at = proc, time.monotonic()
                return True
        return False

    if not args.no_window and not next_window():
        _message_box(f"Arayüz penceresi açılamadı. Tarayıcıda şu adresi aç: {url}")
        launched_at = time.monotonic()
    try:
        while True:
            time.sleep(1)
            if args.no_window:
                continue
            now = time.monotonic()
            if not ui_seen:
                if state.last_poll > launched_at:
                    ui_seen = True
                    watch_process = window is not None and window.poll() is None
                    continue
                failed_fast = window is not None and window.poll() not in (None, 0)
                if failed_fast or now - launched_at > WINDOW_WAIT:
                    if not next_window() and now - launched_at > HEARTBEAT_TIMEOUT * 4:
                        break
                continue
            if watch_process:
                if window.poll() is not None:
                    break
            elif now - (state.last_poll or now) > HEARTBEAT_TIMEOUT and not state.engine.running:
                break
    except KeyboardInterrupt:
        pass
    finally:
        state.engine.stop()
        state.engine.wait(15)
        server.shutdown()
        state.store.close()
        try:
            _instance_file().unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
