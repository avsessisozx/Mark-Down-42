"""Ayrıntılı rapor: sorunu uzaktan incelemek için sürüm, bilgisayar, ayarlar, durum ve günlük tek metinde.

Çerez, oturum anahtarı, şifre ve erişim bilgisi içermez (varsa silinir).
"""

from __future__ import annotations

import json
import platform
import re
import sys
from typing import Any, Dict, List

from . import __version__

MAX_CHARS = 150_000

_SECRET = re.compile(
    r"(?i)(sessionid|csrftoken|ds_user_id|ig_did|datr|mid|rur|ig_nrcb|authorization|password|passwd|token)"
    r"([\"']?\s*[:=]\s*[\"']?)[^;\s&,\"'}]+")


def redact(text: str) -> str:
    return _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}[silindi]", text)


def build(settings: Dict[str, Any], snap: Dict[str, Any]) -> str:
    out: List[str] = ["Reels Gözlemci raporu", f"Sürüm: {__version__}",
                      f"Sistem: {platform.platform()} · Python {sys.version.split()[0]}",
                      f"Tarayıcı: {snap.get('browser') or '-'}"]
    s = dict(settings)
    s.pop("out_dir", None)
    out.append("\n--- Ayarlar ---")
    out.append(json.dumps(s, ensure_ascii=False))
    out.append("\n--- Durum ---")
    for k in ("phase", "message", "logged_in", "auto", "paused", "views", "reels_known", "responses_seen",
              "responses_with_media", "scroll_fail", "current_code", "url", "out_dir", "live"):
        out.append(f"{k}: {snap.get(k)}")
    cur = snap.get("current") or {}
    if cur:
        out.append("current: " + json.dumps(cur, ensure_ascii=False))
    fields = snap.get("numeric_fields") or []
    if fields:
        out.append(f"\n--- Ham kayıttaki sayaç alanları ({len(fields)} satır; izlenme/paylaşım/gönderme adını bulmak için) ---")
        out.extend(fields)
    samples = snap.get("screen_samples") or []
    out.append(f"\n--- Ekrandan okunan sayaç adayları (etiket=metin; {snap.get('screen_filled', 0)} Reel'de alan dolduruldu) ---")
    out.extend(samples or ["(hiç sayaç adayı okunamadı)"])
    acc = snap.get("account_data") or {}
    if acc:
        out.append("\n--- Hesap verisi (içe aktarılan kayıt sayıları) ---")
        out.append(json.dumps(acc, ensure_ascii=False))
    logs = [f"{e.get('t', '')} [{e.get('level', '')}] {str(e.get('text', '')).replace(chr(10), ' ')}"
            for e in (snap.get("logs") or [])]
    out.append(f"\n--- Günlük ({len(logs)} satır) ---")
    head = sum(len(x) + 1 for x in out)
    body: List[str] = []
    size = 0
    truncated = False
    for line in reversed(logs):
        if head + size + len(line) + 1 > MAX_CHARS:
            truncated = True
            break
        body.insert(0, line)
        size += len(line) + 1
    if truncated:
        out.append("(başı kısaltıldı)")
    out.extend(body)
    return redact("\n".join(out) + "\n")
