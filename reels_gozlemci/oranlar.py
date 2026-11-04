"""Oranlar ve "sıra dışı" Reel işaretleme (kural tabanlı).

Ham sayı yerine oran bakarız; böylece "küçük hesap, çok izlenme" ve "emek verilmiş ama az izlenen" ayrılır.
Takipçi sayısı Instagram'ın akış yanıtında çoğu zaman yoktur; yoksa takipçi oranı hesaplanmaz ve veri setinin kendi
ortancasına göre kıyaslarız (uyarı eklenir).
"""

from __future__ import annotations

import math
import statistics
import time
from typing import Any, Dict, List, Optional


def _ratio(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b <= 0:
        return None
    return a / b


def reel_metrics(r: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    now = now or time.time()
    plays, likes, comments = r.get("play_count"), r.get("like_count"), r.get("comment_count")
    sends, reshares, followers = r.get("send_count"), r.get("reshare_count"), r.get("follower_count")
    age_h = None
    if r.get("taken_at"):
        age_h = max(1.0, (now - float(r["taken_at"])) / 3600.0)
    return {
        "plays_per_follower": _ratio(plays, followers),
        "likes_per_play": _ratio(likes, plays),
        "comments_per_like": _ratio(comments, likes),
        "sends_per_play": _ratio(sends, plays),
        "reshares_per_play": _ratio(reshares, plays),
        "plays_per_hour": _ratio(plays, age_h),
        "age_hours": age_h,
    }


def _z(values: List[float], x: float) -> Optional[float]:
    if len(values) < 8:
        return None
    logs = [math.log10(v + 1) for v in values]
    sd = statistics.pstdev(logs)
    if sd <= 1e-9:
        return None
    return (math.log10(x + 1) - statistics.mean(logs)) / sd


def analyze(reels: List[Dict[str, Any]], now: Optional[float] = None) -> Dict[str, Any]:
    """Reel listesi (store.export_reels çıktısı) -> sıra dışı olanlar ve genel oranlar."""
    now = now or time.time()
    organic = [r for r in reels if not r.get("is_ad")]
    rows: List[Dict[str, Any]] = []
    for r in organic:
        m = reel_metrics(r, now)
        rows.append({"code": r["code"], "username": r.get("username") or "", "caption": (r.get("caption") or "")[:100],
                     "plays": r.get("play_count"), "likes": r.get("like_count"), "followers": r.get("follower_count"),
                     "n_views": r.get("n_views") or 0, **m})

    plays = [x["plays"] for x in rows if x["plays"] is not None]
    have_followers = sum(1 for x in rows if x["plays_per_follower"] is not None)
    over: List[Dict[str, Any]] = []
    under: List[Dict[str, Any]] = []
    for x in rows:
        flags: List[str] = []
        score: Optional[float] = None
        if x["plays_per_follower"] is not None and x["plays_per_follower"] >= 20 and (x["plays"] or 0) >= 10_000:
            flags.append(f"takipçisinin {x['plays_per_follower']:.0f} katı izlenme")
            score = x["plays_per_follower"]
        z = _z(plays, x["plays"]) if x["plays"] is not None else None
        if z is not None and z >= 1.5:
            flags.append("akışındaki diğerlerine göre çok yüksek izlenme")
            score = max(score or 0, z)
        if x["plays_per_hour"] is not None and plays:
            med_h = statistics.median([y["plays_per_hour"] for y in rows if y["plays_per_hour"] is not None] or [0])
            if med_h > 0 and x["plays_per_hour"] >= 10 * med_h:
                flags.append("yaşına göre çok hızlı yayılıyor")
        if flags:
            over.append({**x, "flags": flags, "score": score or 0})
        # emek/etkileşim yüksek ama izlenme düşük: beğeni/izlenme çok yüksek, izlenme akış ortancasının altında
        if (x["likes_per_play"] is not None and x["likes_per_play"] >= 0.15 and plays and x["plays"] is not None
                and x["plays"] < statistics.median(plays) and (x["likes"] or 0) >= 50):
            under.append({**x, "flags": ["izleyenlerin çoğu beğenmiş ama az kişiye gösterilmiş"]})
    over.sort(key=lambda d: -(d["score"] or 0))
    return {
        "reels": len(organic), "with_plays": len(plays), "with_followers": have_followers,
        "overperformers": over[:40], "underexposed": under[:25],
        "notes": ([] if have_followers else ["Takipçi sayısı akış yanıtında gelmiyor; takipçiye göre oran hesaplanamadı, "
                                             "akışındaki diğer Reel'lere göre kıyaslandı."])
                 + ([] if len(plays) >= 8 else ["İzlenme sayısı olan en az 8 Reel gerekir; henüz yeterli veri yok."]),
    }
