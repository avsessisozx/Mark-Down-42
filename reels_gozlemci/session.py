"""Hesap (oturum) bilgisi: yapıştırılan kodu ayrıştırır ve tarayıcıya yerleştirilecek çerezleri hazırlar.

Instagram İndirici'deki yöntemin aynısı: kullanıcı tanıdığı Chrome'da giriş yapar, küçük bir eklenti oturum
çerezlerini bu programa (127.0.0.1) gönderir; program onları kendi tarayıcısına yerleştirir.
Çerez değerleri günlüğe ve rapora yazılmaz.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List
from urllib.parse import urlsplit

SESSION_NAMES = ("sessionid", "csrftoken", "ds_user_id", "mid", "ig_did", "datr", "ig_nrcb")
_SESSION_VALUE = re.compile(r"^[A-Za-z0-9%:_.\-]{6,}$")
_DEVICE_VALUE = re.compile(r"^[A-Za-z0-9%:_.\-]{1,200}$")   # mid / ig_did / datr: Instagram'ın cihazı tanıdığı çerezler


def parse_session_input(text: str) -> Dict[str, str]:
    """Yapıştırılan metinden Instagram oturum çerezlerini çıkarır.

    Kabul edilen biçimler: yalın sessionid değeri, ``sessionid=...``, tüm ``Cookie:`` satırı ya da
    geliştirici araçlarından kopyalanan satır (``sessionid<TAB>değer<TAB>...``). Geçersizse boş sözlük döner.
    """
    raw = (text or "").strip().strip("'\"").strip()
    if not raw:
        return {}
    found: Dict[str, str] = {}
    body = re.sub(r"^\s*cookie:\s*", "", raw, flags=re.I)
    if "=" in body:
        for part in re.split(r"[;\n]", body):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip().lower()
            value = value.strip().strip("'\"")
            if key in SESSION_NAMES and value:
                found[key] = value
    if "sessionid" not in found:
        m = re.match(r"^\s*sessionid\s+(\S+)", raw, flags=re.I)
        if m:
            found["sessionid"] = m.group(1)
    if "sessionid" not in found and re.fullmatch(r"[^\s;=]+", raw):
        found["sessionid"] = raw
    sid = found.get("sessionid", "").replace(":", "%3A")
    if not _SESSION_VALUE.match(sid):
        return {}
    found["sessionid"] = sid
    for key in SESSION_NAMES[1:]:
        if key in found and not _DEVICE_VALUE.match(found[key]):
            del found[key]
    if "ds_user_id" not in found:
        m = re.match(r"^(\d{5,})%3A", sid)
        if m:
            found["ds_user_id"] = m.group(1)
    return {k: v for k, v in found.items() if k in SESSION_NAMES}


def clean_cookies(raw: Any) -> Dict[str, str]:
    """Eklentiden gelen {ad: değer} sözlüğünü doğrular (yalnız bilinen adlar, makul değerler)."""
    if not isinstance(raw, dict):
        return {}
    pairs = "; ".join(f"{k}={v}" for k, v in raw.items() if isinstance(k, str) and isinstance(v, str))
    return parse_session_input(pairs)


def cookie_items(cookies: Dict[str, str], base_url: str) -> List[Dict[str, Any]]:
    """Playwright'ın add_cookies'ine verilecek kayıtlar."""
    host = urlsplit(base_url).hostname or "www.instagram.com"
    domain = ".instagram.com" if host.endswith("instagram.com") else host
    secure = base_url.startswith("https")
    expires = time.time() + 365 * 24 * 3600
    return [{"name": n, "value": v, "domain": domain, "path": "/", "expires": expires,
             "httpOnly": n == "sessionid", "secure": secure} for n, v in cookies.items()]
