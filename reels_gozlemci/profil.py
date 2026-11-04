"""Profilim raporu: indirilen hesap verisi + programın topladığı akış -> "Instagram seni nasıl görüyor olabilir".

Kural tabanlıdır (yapay zekâ yok). Çıkan şey Instagram'ın gerçek profili DEĞİL, senin verinden kurulmuş bir yeniden inşadır:
Instagram'ın içeride tuttuğu puanlar paketin içinde yoktur. Paketteki Instagram'ın kendi etiketleri (ilgi alanları) ayrı bölümde
gösterilir. Nedenler "olası bağlantı"dır, kesin sebep değildir.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

_TAG = re.compile(r"#(\w+)", re.UNICODE)

CATEGORY_NAMES = {
    "begeni": "beğenilen gönderi", "yorum_begeni": "beğenilen yorum", "kayit": "kaydedilen gönderi", "yorum": "yorum",
    "takip": "takip edilen hesap", "takipci": "takipçi", "etiket_takip": "takip edilen etiket",
    "izlenen_video": "izlenen video kaydı", "izlenen_gonderi": "görüntülenen gönderi kaydı",
    "onerilen_hesap": "görüntülenen önerilen hesap", "izlenen_reklam": "görüntülenen reklam",
    "ilgi_alani": "Instagram'ın ilgi alanı etiketi", "reklam_veren": "reklam veren", "arama": "arama",
}


def _top(counter: Counter, n: int) -> List[Dict[str, Any]]:
    return [{"label": k, "n": v} for k, v in counter.most_common(n)]


def build_profile(store: Any) -> Dict[str, Any]:
    summary = store.account_summary()
    items = store.account_rows()
    reels = store.reels_for_profile()
    out: Dict[str, Any] = {"has_account_data": bool(items), "has_feed": bool(reels), "summary": [
        {"key": k, "label": CATEGORY_NAMES.get(k, k), "n": n} for k, n in sorted(summary.items(), key=lambda kv: -kv[1])]}

    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)

    def users(cat: str) -> Counter:
        return Counter(i["username"] for i in by_cat.get(cat, []) if i.get("username"))

    following = set(users("takip"))
    liked, saved, commented = users("begeni"), users("kayit"), users("yorum")
    watched = users("izlenen_video") + users("izlenen_gonderi")
    suggested = users("onerilen_hesap")

    # --- Sen: hesap verisinden
    engaged = liked + saved + commented
    out["you"] = {
        "following": len(following),
        "top_engaged": [{"label": "@" + u, "n": n, "liked": liked.get(u, 0), "saved": saved.get(u, 0),
                         "commented": commented.get(u, 0)} for u, n in engaged.most_common(12)],
        "top_watched": _top(Counter({"@" + u: n for u, n in watched.items()}), 12),
        "comment_tags": _top(Counter(t.lower() for i in by_cat.get("yorum", []) for t in _TAG.findall(i.get("text") or "")), 12),
        "searches": _top(Counter((i.get("text") or i.get("username") or "") for i in by_cat.get("arama", [])
                                 if (i.get("text") or i.get("username"))), 12),
    }
    # Instagram'ın kendi etiketleri (varsa) — raporun en değerli bölümü
    topics = Counter()
    for i in by_cat.get("ilgi_alani", []):
        if i.get("text"):
            topics[i["text"]] += 1
    out["instagram_labels"] = {
        "topics": [t for t, _ in topics.most_common(80)],
        "advertisers": len(by_cat.get("reklam_veren", [])),
        "note": ("Bunlar Instagram'ın paketine koyduğu etiketlerdir. Hangilerinin neden verildiğini söylemez."
                 if topics else "Paketinde ilgi alanı dosyası bulunamadı (Instagram bunu ülkeye/zamana göre değiştiriyor)."),
    }

    # --- Akış: programın topladığı
    total = len(reels)
    organic = [r for r in reels if not r.get("is_ad")]
    feed_users = Counter((r.get("username") or "").lower() for r in organic if r.get("username"))
    def share(pred) -> float:
        base = [r for r in organic if r.get("username")]
        return (sum(1 for r in base if pred(r["username"].lower())) / len(base)) if base else 0.0

    feed = {
        "reels": total, "ads": total - len(organic),
        "top_accounts": [{"label": "@" + u, "n": n} for u, n in feed_users.most_common(12)],
        "top_tags": _top(Counter(t.lower() for r in organic for t in _TAG.findall(r.get("caption") or "")), 12),
        "top_music": _top(Counter(r["music"] for r in organic if r.get("music")), 12),
    }
    if items:
        feed["share_following"] = round(share(lambda u: u in following), 3)
        feed["share_liked_before"] = round(share(lambda u: u in liked), 3)
        feed["share_saved_before"] = round(share(lambda u: u in saved), 3)
        feed["share_commented_before"] = round(share(lambda u: u in commented), 3)
        feed["share_watched_before"] = round(share(lambda u: u in watched), 3)
        feed["share_unexplained"] = round(share(lambda u: not (u in following or u in engaged or u in watched)), 3)
    out["feed"] = feed

    # --- Reel başına olası bağlantılar
    reasons: List[Dict[str, Any]] = []
    if items:
        for r in sorted(reels, key=lambda x: -(x.get("n_views") or 0)):
            u = (r.get("username") or "").lower()
            why: List[str] = []
            if r.get("is_ad"):
                why.append("Reklam")
            if u:
                if u in following:
                    why.append("Takip ediyorsun")
                if liked.get(u):
                    why.append(f"Bu hesabın {liked[u]} gönderisini beğenmişsin")
                if saved.get(u):
                    why.append(f"Bu hesaptan {saved[u]} gönderi kaydetmişsin")
                if commented.get(u):
                    why.append(f"Bu hesaba {commented[u]} yorum yapmışsın")
                if watched.get(u):
                    why.append(f"Bu hesabın {watched[u]} videosunu/gönderisini izlemişsin")
                if suggested.get(u):
                    why.append("Daha önce önerilen hesaplar arasında görmüşsün")
            if not why:
                why.append("Hesap verinde bağlantı bulunamadı")
            reasons.append({"code": r["code"], "username": r.get("username") or "", "n_views": r.get("n_views") or 0,
                            "why": why, "explained": why[0] not in ("Hesap verinde bağlantı bulunamadı",)})
            if len(reasons) >= 120:
                break
    out["reasons"] = reasons
    out["caveats"] = [
        "Bu rapor Instagram'ın gerçek profili değil; senin verinden kurulmuş olası bağlantıların listesidir.",
        "Programın kendi otomatik kaydırması da Instagram'ın seni nasıl gördüğünü etkiler; yedek hesapla çalış.",
        "Yalnızca Reels'i kapsar (ana akış, Keşfet ve Hikayeler yok).",
    ]
    return out
