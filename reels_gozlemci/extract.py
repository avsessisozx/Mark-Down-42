"""Sayfanın kendi aldığı JSON yanıtlarından Reel bilgilerini ayıklar.

Instagram'ın yanıt biçimi sık değişir; bu yüzden belirli bir yola (edges -> node -> media) bağlı kalmayız.
Yanıtın içinde "code" + (beğeni / video / klip alanı) taşıyan her nesne bir Reel/gönderi sayılır.
Sadece sayfanın zaten ekrana getirdiği veriyi okuruz; Instagram'a kendimiz istek göndermeyiz.
"""

from __future__ import annotations

import json
import re
import zlib
from typing import Any, Dict, Iterator, List, Optional

_HASHTAG = re.compile(r"#(\w+)", re.UNICODE)
_DROP_KEYS = {"video_dash_manifest", "image_versions2", "video_versions", "thumbnail_url", "display_uri",
              "carousel_media", "sharing_friction_info", "preview", "profile_pic_url", "hd_profile_pic_url_info"}
_MAX_STR = 2000


def parse_response_text(text: str) -> List[Any]:
    """JSON ya da satır satır JSON (akışlı GraphQL) yanıtını nesne listesine çevirir."""
    text = text.strip()
    if text.startswith("for (;;);"):
        text = text[len("for (;;);"):]
    try:
        return [json.loads(text)]
    except ValueError:
        pass
    out: List[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(("{", "[")):
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def walk(obj: Any, max_nodes: int = 400_000) -> Iterator[Dict[str, Any]]:
    """İç içe yapıdaki bütün sözlükleri gezer."""
    stack = [obj]
    seen = 0
    while stack and seen < max_nodes:
        cur = stack.pop()
        seen += 1
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def is_media(d: Dict[str, Any]) -> bool:
    code = d.get("code")
    if not isinstance(code, str) or not (5 <= len(code) <= 20):
        return False
    if "pk" not in d and "id" not in d:
        return False
    return any(k in d for k in ("like_count", "video_versions", "clips_metadata", "play_count",
                                "comment_count", "product_type", "video_duration"))


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _first_int(d: Dict[str, Any], *keys: str) -> Optional[int]:
    for k in keys:
        v = _int(d.get(k))
        if v is not None:
            return v
    return None


def _dig(d: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _music(d: Dict[str, Any]) -> str:
    meta = d.get("clips_metadata") if isinstance(d.get("clips_metadata"), dict) else {}
    info = _dig(meta, "music_info", "music_asset_info")
    if isinstance(info, dict) and (info.get("title") or info.get("display_artist")):
        return f"{info.get('title') or ''} — {info.get('display_artist') or ''}".strip(" —")
    orig = meta.get("original_sound_info") if isinstance(meta, dict) else None
    if isinstance(orig, dict):
        who = _dig(orig, "ig_artist", "username") or ""
        title = orig.get("original_audio_title") or ""
        if title or who:
            return f"{title} (özgün ses{', ' + who if who else ''})"
    return ""


def _slim(obj: Any, depth: int = 0) -> Any:
    """Ham kaydı küçültür: büyük görsel/video listelerini ve çok uzun metinleri atar."""
    if depth > 14:
        return None
    if isinstance(obj, dict):
        return {k: _slim(v, depth + 1) for k, v in obj.items() if k not in _DROP_KEYS}
    if isinstance(obj, list):
        return [_slim(v, depth + 1) for v in obj[:40]]
    if isinstance(obj, str) and len(obj) > _MAX_STR:
        return obj[:_MAX_STR] + "…"
    return obj


def media_record(d: Dict[str, Any]) -> Dict[str, Any]:
    caption = d.get("caption")
    caption_text = caption.get("text") if isinstance(caption, dict) else (caption if isinstance(caption, str) else "")
    caption_text = caption_text or ""
    user = d.get("user") if isinstance(d.get("user"), dict) else (d.get("owner") if isinstance(d.get("owner"), dict) else {})
    duration = d.get("video_duration")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool):
        duration = None
    liked = d.get("has_liked", d.get("viewer_has_liked"))
    ad = bool(d.get("ad_id") or d.get("is_ad") or d.get("ad_action") or d.get("is_sponsored"))
    raw = json.dumps(_slim(d), ensure_ascii=False, separators=(",", ":"))
    return {
        "code": d["code"],
        "pk": str(d.get("pk") or d.get("id") or ""),
        "username": str(user.get("username") or ""),
        "full_name": str(user.get("full_name") or ""),
        "caption": caption_text,
        "hashtags": " ".join("#" + h for h in _HASHTAG.findall(caption_text)),
        "like_count": _first_int(d, "like_count"),
        "comment_count": _first_int(d, "comment_count"),
        "play_count": _first_int(d, "play_count", "ig_play_count", "view_count", "video_view_count"),
        "reshare_count": _first_int(d, "reshare_count", "share_count"),
        "duration": float(duration) if duration is not None else None,
        "music": _music(d),
        "product_type": str(d.get("product_type") or ""),
        "taken_at": _first_int(d, "taken_at", "taken_at_timestamp"),
        "is_ad": 1 if ad else 0,
        "has_liked": (1 if liked else 0) if liked is not None else None,
        "likes_hidden": 1 if d.get("like_and_view_counts_disabled") else 0,
        "location": str(_dig(d, "location", "name") or ""),
        "raw": zlib.compress(raw.encode("utf-8"), 6),
    }


def extract_media(text: str) -> List[Dict[str, Any]]:
    """Yanıt metnindeki bütün Reel/gönderi kayıtlarını döndürür (aynı kod bir kez)."""
    if '"code"' not in text:
        return []
    found: Dict[str, Dict[str, Any]] = {}
    for doc in parse_response_text(text):
        for d in walk(doc):
            if is_media(d):
                rec = media_record(d)
                old = found.get(rec["code"])
                if old is None or len(rec["raw"]) > len(old["raw"]):
                    found[rec["code"]] = rec
    return list(found.values())
