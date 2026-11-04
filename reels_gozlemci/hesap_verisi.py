"""Instagram'ın "bilgilerini indir" paketini (JSON biçimi) okur ve program veritabanına koyar.

Önemli:
- Mesajlar (DM) HİÇ okunmaz; `messages` / `inbox` klasörleri atlanır.
- Dosya yapısı Instagram'ın elinde ve değişebilir; bu yüzden belirli bir şemaya bağlanmayız. Her dosyanın yolundan bir
  kategori çıkarırız (beğeni, kayıt, yorum, takip, izlenen video, ilgi alanı, arama…) ve kayıtlardan hesap adı, gönderi/Reel
  kodu, zaman ve metin alanlarını genel kurallarla ayıklarız. Tanınmayan dosyalar özetin "tanınmayan" listesine yazılır
  (gerçek paketle uyumu doğrulamak için).
- Bu gerçek bir kullanıcı paketiyle henüz doğrulanmadı (bkz. DURUM.md); sahte paketle sınandı.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

_CODE_RX = re.compile(r"instagram\.com/(?:[A-Za-z0-9._]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]{5,})")
_USER_HREF_RX = re.compile(r"instagram\.com/(?:_u/)?([A-Za-z0-9._]{1,30})/?(?:\?.*)?$")
_USERNAME_RX = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_SKIP_DIRS = ("messages", "inbox", "message_requests", "your_instagram_activity/messages")

# (kategori, yolda aranan ifadeler) — ilk eşleşen kazanır; sıra önemli.
_CATEGORY_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    ("yorum_begeni", ("liked_comments",)),
    ("begeni", ("liked_posts", "likes/", "/likes", "reel_likes")),
    ("kayit", ("saved_posts", "saved_collections", "/saved/", "saved_")),
    ("yorum", ("post_comments", "reels_comments", "reel_comments", "/comments/", "comments_")),
    ("takip", ("close_friends",)),
    ("izlenen_video", ("videos_watched", "reels_watched")),
    ("izlenen_gonderi", ("posts_viewed",)),
    ("onerilen_hesap", ("suggested_accounts_viewed",)),
    ("izlenen_reklam", ("ads_viewed", "ads_clicked")),
    ("ilgi_alani", ("your_topics", "/topics", "ads_interests", "interests", "recommended_topics")),
    ("reklam_veren", ("advertisers_using", "advertisers", "ads_and_businesses")),
    ("arama", ("account_searches", "word_or_phrase_searches", "recent_searches", "searches")),
]


def category_for(path: str) -> Optional[str]:
    low = path.lower().replace("\\", "/")
    stem = low.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    # "followers_and_following" klasör adı hem takipçi hem takip dosyasını kapsar: dosya adına bakılır.
    if stem.startswith("followers"):
        return "takipci"
    if stem.startswith("following_hashtags"):
        return "etiket_takip"
    if stem.startswith("following"):
        return "takip"
    for cat, needles in _CATEGORY_RULES:
        if any(n in low for n in needles):
            return cat
    return None


def fix_text(value: Any) -> str:
    """Instagram dışa aktarması UTF-8'i Latin-1 gibi kodlayabiliyor ("Ã§" yerine "ç"); mümkünse düzeltir."""
    if not isinstance(value, str):
        return ""
    if not any(0x80 <= ord(c) <= 0xFF for c in value):
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _records(data: Any) -> Iterator[Dict[str, Any]]:
    """Dosyadaki kayıt listelerini bulur (kök liste ya da kökteki sözlüğün liste değerleri; bir kat iç içe de olur)."""
    if isinstance(data, list):
        for x in data:
            if isinstance(x, dict):
                yield x
        return
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                for x in v:
                    if isinstance(x, dict):
                        yield x
            elif isinstance(v, dict):
                for vv in v.values():
                    if isinstance(vv, list):
                        for x in vv:
                            if isinstance(x, dict):
                                yield x


def _ts(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 1_000_000_000:
        return int(value)
    return None


def parse_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Bir kaydı {username, code, ts, text} olarak sadeleştirir (bulunmayan alan None)."""
    username: Optional[str] = None
    code: Optional[str] = None
    ts: Optional[int] = _ts(rec.get("timestamp"))
    texts: List[str] = []

    def take_href(href: Any) -> None:
        nonlocal code, username
        if not isinstance(href, str) or not href:
            return
        m = _CODE_RX.search(href)
        if m and not code:
            code = m.group(1)
            return
        u = _USER_HREF_RX.search(href)
        if u and username is None and u.group(1).lower() not in ("p", "reel", "reels", "tv", "explore", "accounts"):
            username = u.group(1)

    title = rec.get("title")
    if isinstance(title, str) and title.strip():
        t = fix_text(title.strip())
        if _USERNAME_RX.match(t):
            username = t
        else:
            texts.append(t)
    for item in rec.get("string_list_data") or []:
        if not isinstance(item, dict):
            continue
        take_href(item.get("href"))
        ts = ts or _ts(item.get("timestamp"))
        val = item.get("value")
        if isinstance(val, str) and val.strip():
            texts.append(fix_text(val.strip()))
    smd = rec.get("string_map_data")
    if isinstance(smd, dict):
        for key, item in smd.items():
            if not isinstance(item, dict):
                continue
            k = str(key).lower()
            take_href(item.get("href"))
            ts = ts or _ts(item.get("timestamp"))
            val = item.get("value")
            if isinstance(val, str) and val.strip():
                v = fix_text(val.strip())
                if any(w in k for w in ("author", "owner", "username", "account", "sahip", "yazar")) and _USERNAME_RX.match(v):
                    username = username or v
                else:
                    texts.append(v)
    for key in ("href", "url", "link"):
        take_href(rec.get(key))
    return {"username": username, "code": code, "ts": ts, "text": " | ".join(dict.fromkeys(texts))[:600] or None}


def _iter_sources(path: Path) -> Iterator[Tuple[str, bytes]]:
    """(paket içi yol, bayt) çiftleri; klasör, ZIP ya da tek dosya olabilir. Mesaj klasörleri atlanır."""
    def skipped(name: str) -> bool:
        low = name.lower().replace("\\", "/")
        parts = low.split("/")
        return any(p in ("messages", "inbox", "message_requests") for p in parts)

    if path.is_dir():
        for f in sorted(path.rglob("*")):
            rel = f.relative_to(path).as_posix()
            if f.is_file() and not skipped(rel):
                yield rel, f.read_bytes() if f.suffix.lower() == ".json" and f.stat().st_size < 150_000_000 else b""
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir() or skipped(info.filename):
                    continue
                if info.filename.lower().endswith(".json") and info.file_size < 150_000_000:
                    yield info.filename, z.read(info)
                else:
                    yield info.filename, b""
    else:
        yield path.name, path.read_bytes()


def import_package(path: Path, store: Any) -> Dict[str, Any]:
    """Paketi okuyup veritabanındaki hesap verisini YENİLER (öncekini siler). Özet sözlüğü döndürür."""
    path = Path(path)
    summary: Dict[str, Any] = {"path": str(path), "json_files": 0, "html_files": 0, "categories": {}, "unrecognized": [],
                               "errors": [], "items": 0}
    rows: List[tuple] = []
    for name, raw in _iter_sources(path):
        low = name.lower()
        if low.endswith(".html"):
            summary["html_files"] += 1
            continue
        if not low.endswith(".json") or not raw:
            continue
        summary["json_files"] += 1
        cat = category_for(name)
        if cat is None:
            if len(summary["unrecognized"]) < 40:
                summary["unrecognized"].append(name)
            continue
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as exc:
            summary["errors"].append(f"{name}: {exc}")
            continue
        n = 0
        for rec in _records(data):
            p = parse_record(rec)
            if not (p["username"] or p["code"] or p["text"]):
                continue
            rows.append((cat, name, p["ts"], (p["username"] or "").lower() or None, p["code"], p["text"]))
            n += 1
        if n:
            summary["categories"][cat] = summary["categories"].get(cat, 0) + n
    if summary["json_files"] == 0:
        summary["errors"].append("JSON dosyası bulunamadı. Instagram'dan indirirken biçim olarak JSON seçilmeli "
                                 "(HTML seçildiyse paketi yeniden iste).")
    store.account_clear()
    store.account_add(rows)
    summary["items"] = len(rows)
    return summary
