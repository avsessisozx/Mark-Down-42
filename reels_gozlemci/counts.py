"""Ekranda görünen sayıları ("1,2 B", "12.4K", "3 Mn", "1.234") tam sayıya çevirir ve sayaç etiketlerini eşler.

Ekrandaki sayılar kısaltmalıdır (yaklaşık); Instagram'ın yanıtındaki sayılar kesindir. Bu yüzden ekran okuması
yalnızca yanıtta o alan yoksa kullanılır ve kaynağı ("ekran") ayrıca işaretlenir.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

_NUM = re.compile(r"^\s*([0-9][0-9.,   ]*)\s*([A-Za-zÇĞİÖŞÜçğıöşü]*)\s*$")

# Soneklerin çarpanları (küçük harfe çevrilmiş). "mn" = milyon (Türkçe), "m" = million, "b" = bin (Türkçe) / billion (İngilizce).
_MULT_TR = {"": 1, "b": 1_000, "bin": 1_000, "k": 1_000, "mn": 1_000_000, "m": 1_000_000, "milyon": 1_000_000,
            "mr": 1_000_000_000, "milyar": 1_000_000_000}
_MULT_EN = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "bn": 1_000_000_000}


def parse_count(text: object, english: bool = False) -> Optional[int]:
    """"1,2 B" -> 1200, "12.4K" -> 12400, "3 Mn" -> 3_000_000, "1.234" -> 1234, "87" -> 87. Anlaşılmazsa None."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    m = _NUM.match(s)
    if not m:
        return None
    num, suffix = m.group(1), m.group(2).lower()
    table = _MULT_EN if english else _MULT_TR
    if suffix not in table:
        table = _MULT_EN if not english else _MULT_TR
        if suffix not in table:
            return None
    mult = table[suffix]
    num = re.sub(r"[   ]", "", num)
    if mult == 1:
        # Ondalık yok: "1.234" ya da "1,234" binlik ayraçtır.
        digits = re.sub(r"[.,]", "", num)
        return int(digits) if digits.isdigit() else None
    # Kısaltmalı: tek bir ayraç ondalıktır ("1,2 B", "12.4K").
    seps = re.findall(r"[.,]", num)
    if len(seps) > 1:
        return None
    clean = num.replace(",", ".")
    try:
        return int(round(float(clean) * mult))
    except ValueError:
        return None


# Etiket (düğmenin erişilebilirlik adı) -> alan. Hem Türkçe hem İngilizce arayüz için.
_LABELS = [
    ("like_count", re.compile(r"\b(beğen|begen|like)", re.I)),
    ("comment_count", re.compile(r"\b(yorum|comment)", re.I)),
    ("reshare_count", re.compile(r"\b(yeniden paylaş|yeniden payla|repost)", re.I)),
    ("send_count", re.compile(r"\b(gönder|gonder|paylaş|paylas|share|send)", re.I)),
    ("play_count", re.compile(r"\b([iİı]zlen|görüntülen|goruntulen|oynat|play|view)", re.I)),
]


def classify_label(label: str) -> Optional[str]:
    """Düğme/simge etiketini sayaç alanına eşler; tanınmazsa None."""
    text = (label or "").strip()
    if not text:
        return None
    # "Yeniden paylaş" "paylaş"tan önce denenmeli (liste sırası bunu sağlar).
    for field, rx in _LABELS:
        if rx.search(text):
            return field
    return None


def fields_from_candidates(cands: Iterable[Dict[str, object]]) -> Dict[str, int]:
    """Ekrandan okunan [{label, text}] adaylarından {alan: sayı} üretir. Aynı alan iki kez gelirse ilki kalır."""
    out: Dict[str, int] = {}
    for c in cands:
        field = classify_label(str(c.get("label") or ""))
        if not field or field in out:
            continue
        n = parse_count(c.get("text"))
        if n is not None:
            out[field] = n
    return out


def summarize_candidates(cands: List[Dict[str, object]], limit: int = 12) -> str:
    """Rapor için: "etiket=metin" listesi (eşleşme olmasa bile, gerçek etiketleri görebilmek için)."""
    parts = []
    for c in cands[:limit]:
        parts.append(f"{str(c.get('label') or '?')[:30]}={str(c.get('text') or '')[:12]}")
    return "; ".join(parts)


def _near(screen: int, exact: Optional[int]) -> bool:
    """Ekrandaki (kısaltılmış olabilir) sayı yanıttaki kesin sayıyla uyumlu mu? "27,8 B" ~ 27855."""
    if exact is None:
        return False
    if screen == exact:
        return True
    if exact < 1000:
        return False
    return abs(screen - exact) <= max(0.051 * exact, 1)


def fields_by_position(cands: Iterable[Dict[str, object]], known: Dict[str, Optional[int]]) -> Dict[str, int]:
    """Etiketsiz sayaçlar için: Reels ekranındaki sıra beğeni, yorum, repost, gönderme'dir.

    Sıra, yanıttan bilinen beğeni ve yorum sayısıyla çapalanır; çapa tutmazsa hiçbir şey yazılmaz
    (yanlış alana sayı yazmaktansa boş bırakmak). Beğeniler gizliyse okunmaz.
    """
    if known.get("likes_hidden"):
        return {}
    nums = [n for n in (parse_count(c.get("text")) for c in cands) if n is not None]
    like, comment, send = known.get("like_count"), known.get("comment_count"), known.get("send_count")
    for i in range(len(nums) - 1):
        yorum_tutuyor = _near(nums[i + 1], comment) if comment else nums[i + 1] == 0
        if _near(nums[i], like) and yorum_tutuyor:
            rest = nums[i + 2:i + 4]
            out: Dict[str, int] = {}
            if len(rest) == 2:
                out["reshare_count"], out["send_count"] = rest
            elif len(rest) == 1:
                if send is not None and _near(rest[0], send):
                    out["send_count"] = rest[0]
                # tek sayı kalıp gönderme sayısı bilinmiyorsa hangisi olduğu belirsiz: yazma
            return out
    return {}
