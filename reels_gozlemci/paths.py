"""Program verisi ve ayarların yeri."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

SCENARIOS = ("fixed", "skip", "full", "mix")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "scenario": "fixed",     # fixed = sabit aralık, skip = hemen kaydır, full = sonuna kadar izle, mix = karışık
    "min_s": 3.0,            # sabit aralığın alt sınırı (sn)
    "max_s": 6.0,            # sabit aralığın üst sınırı (sn)
    "full_cap_s": 60.0,      # "sonuna kadar izle"de en uzun bekleme (sn)
    "auto": True,            # kendisi kaydırsın mı
    "limit": 0,              # bu kadar Reel'den sonra dur (0 = sınırsız)
    "start_url": "https://www.instagram.com/reels/",
    "out_dir": "",
}


def app_data_dir() -> Path:
    override = os.environ.get("REELS_GOZLEMCI_DATA")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ReelsGozlemci"
    else:
        base = Path.home() / ".reels-gozlemci"
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_export_dir() -> Path:
    return Path.home() / "Documents" / "Reels Gozlemci"


def settings_path() -> Path:
    return app_data_dir() / "ayarlar.json"


def _num(value: Any, default: float, low: float, high: float) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return min(high, max(low, x))


def clean_settings(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Gelen ayarları geçerli sınırlara çeker; bilinmeyen anahtarları atar."""
    s = dict(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        if key in raw:
            s[key] = raw[key]
    if s["scenario"] not in SCENARIOS:
        s["scenario"] = DEFAULT_SETTINGS["scenario"]
    s["min_s"] = _num(s["min_s"], 3.0, 0.5, 600)
    s["max_s"] = _num(s["max_s"], 6.0, 0.5, 600)
    if s["max_s"] < s["min_s"]:
        s["min_s"], s["max_s"] = s["max_s"], s["min_s"]
    s["full_cap_s"] = _num(s["full_cap_s"], 60.0, 5, 900)
    s["auto"] = bool(s["auto"])
    s["limit"] = int(_num(s["limit"], 0, 0, 100000))
    if not isinstance(s["start_url"], str) or not s["start_url"].startswith(("http://", "https://")):
        s["start_url"] = DEFAULT_SETTINGS["start_url"]
    s["out_dir"] = s["out_dir"] if isinstance(s["out_dir"], str) else ""
    return s


def load_settings() -> Dict[str, Any]:
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return clean_settings(raw)
    except (OSError, ValueError):
        pass
    return clean_settings({})


def save_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    s = clean_settings(settings)
    try:
        settings_path().write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return s


def screen_size() -> tuple:
    """Ekran boyutu (yalnız pencere yerleşimi için; bulunamazsa 1920x1080)."""
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            w, h = int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass
    return 1920, 1080
