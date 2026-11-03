"""Program verisi ve ayarların yeri."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SCENARIOS = ("fixed", "skip", "full", "mix")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "scenario": "fixed",     # fixed = sabit aralık, skip = hemen kaydır, full = sonuna kadar izle, mix = karışık
    "min_s": 3.0,            # sabit aralığın alt sınırı (sn)
    "max_s": 6.0,            # sabit aralığın üst sınırı (sn)
    "full_cap_s": 60.0,      # "sonuna kadar izle"de en uzun bekleme (sn)
    "auto": True,            # kendisi kaydırsın mı
    "limit": 0,              # bu kadar Reel'den sonra dur (0 = sınırsız)
    "start_url": "https://www.instagram.com/reels/",
    "out_dir": "",           # verilerin yazılacağı klasör (boş = Belgelerim\\Reels Gozlemci)
    "live_write": True,      # her izlemeyi seçili klasördeki canli-gorunumler.csv dosyasına hemen yaz
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


def _known_folder(data1: int, data2: int, data3: int, data4: tuple) -> Optional[Path]:
    """Windows'un gerçek özel klasörü (Belgeler başka sürücüye/OneDrive'a taşınmış olsa bile)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                        ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

        guid = GUID(data1, data2, data3, (ctypes.c_ubyte * 8)(*data4))
        ptr = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(ptr)) == 0:
            value = ptr.value
            ctypes.windll.ole32.CoTaskMemFree(ptr)
            if value:
                return Path(value)
    except Exception:
        pass
    return None


def documents_dir() -> Path:
    # FOLDERID_Documents = {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
    found = _known_folder(0xFDD39AD0, 0x238F, 0x46AF, (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
    return found or Path.home() / "Documents"


def default_export_dir() -> Path:
    return documents_dir() / "Reels Gozlemci"


def fallback_export_dir() -> Path:
    """Seçilen klasöre yazılamazsa kullanılan, her zaman yazılabilen yedek klasör."""
    return app_data_dir() / "disa-aktarma"


def check_writable(folder: Path) -> Optional[str]:
    """Klasör oluşturulup içine dosya yazılabiliyor mu? Sorun yoksa None, varsa hatanın metni."""
    import uuid
    try:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".yazma-denemesi-{uuid.uuid4().hex[:8]}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


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
    s["out_dir"] = s["out_dir"].strip() if isinstance(s["out_dir"], str) else ""
    s["live_write"] = bool(s["live_write"])
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
