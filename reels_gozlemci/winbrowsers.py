"""Bilgisayardaki tarayıcıları bulur, sağlam olup olmadıklarını denetler; gerekirse kendi Chromium'unu indirir.

Edge'in güncellemesi yarım kalınca <ad>_elf.dll eksilebiliyor; böyle bir tarayıcıya takılmayıp Chrome'a
ya da programın kendi indirdiği Chromium'a geçilir. (Instagram İndirici projesinden alındı.)
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

from .paths import app_data_dir

_VERSION_DIR = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def suppress_windows_error_dialogs() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    except Exception:
        pass


def browser_healthy(exe: Optional[str]) -> bool:
    if not exe:
        return False
    path = Path(exe)
    if not path.is_file():
        return False
    if sys.platform != "win32":
        return True
    elf_name = f"{path.stem}_elf.dll"
    version_dirs = [d for d in path.parent.iterdir() if d.is_dir() and _VERSION_DIR.match(d.name)]
    if not version_dirs:
        return False
    return all((d / elf_name).is_file() for d in version_dirs)


def _program_dirs() -> List[Path]:
    out = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(Path(base))
    return out


def _find(*parts: str) -> Optional[str]:
    if sys.platform != "win32" or os.environ.get("REELS_GOZLEMCI_FORCE_BUNDLED"):
        return None
    for base in _program_dirs():
        exe = base.joinpath(*parts)
        if exe.is_file():
            return str(exe)
    return None


def find_edge() -> Optional[str]:
    return _find("Microsoft", "Edge", "Application", "msedge.exe")


def find_chrome() -> Optional[str]:
    return _find("Google", "Chrome", "Application", "chrome.exe")


def healthy_edge() -> Optional[str]:
    exe = find_edge()
    return exe if browser_healthy(exe) else None


def healthy_chrome() -> Optional[str]:
    exe = find_chrome()
    return exe if browser_healthy(exe) else None


def bundled_browsers_dir() -> Path:
    return app_data_dir() / "tarayici-bileseni"


def prepare_playwright_env() -> None:
    """Derlenmiş programda Playwright tarayıcıyı kalıcı bir klasörde arasın/indirsin."""
    if is_frozen() and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers_dir())


def bundled_chromium_exe() -> Optional[str]:
    base = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or bundled_browsers_dir())
    for pattern in ("chromium-*/chrome-win*/chrome.exe", "chromium-*/chrome-linux*/chrome",
                    "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium"):
        hits = sorted(glob.glob(str(base / pattern)))
        if hits:
            return hits[-1]
    return None


def install_bundled_chromium(log: Callable[[str, str], None]) -> bool:
    """Playwright'ın Chromium'unu bir kereye mahsus indirir (~150 MB)."""
    prepare_playwright_env()
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
    except Exception as exc:
        log("error", f"Tarayıcı bileşeni indirilemedi: {exc}")
        return False
    node, cli = compute_driver_executable()
    env = get_driver_env()
    env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(bundled_browsers_dir()))
    log("info", "Tarayıcı bileşeni indiriliyor (bir kereye mahsus, ~150 MB)…")
    try:
        result = subprocess.run([node, cli, "install", "chromium"], env=env, capture_output=True, timeout=1800,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        log("error", f"Tarayıcı bileşeni indirilemedi: {exc}")
        return False
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
        log("error", "Tarayıcı bileşeni indirilemedi: " + " ".join(tail))
        return False
    log("ok", "Tarayıcı bileşeni indirildi.")
    return True
