"""Analiz ekranını denemek için sahte ama gerçekçi veri üretir (test ve ekran görüntüsü için)."""

import json
import random
import time

from reels_gozlemci import extract
from reels_gozlemci.store import Store

ACCOUNTS = ["laykashif", "kedi.videolari", "motovlog_tr", "yemek.atolyesi", "komik_anlar", "spor.haberleri", "gezgin.ayse",
            "teknoloji.tr", "dans.studyo", "doga_belgesel", "oyun.dunyasi", "makyaj.sanati", "araba.tutkusu", "mizah_kralı"]
TAGS = ["#foryou", "#viral", "#komik", "#yemek", "#moto", "#doga", "#spor", "#teknoloji", "#dans", "#gezi", "#oyun", "#kedi", "#araba"]
MUSIC = ["Orijinal ses (özgün ses, laykashif)", "Şarkı A — Sanatçı 1", "Şarkı B — Sanatçı 2", "Trend Ses — DJ Mix", "Orijinal ses (özgün ses, motovlog_tr)",
         "Yaz Hiti — Sanatçı 3", "Lo-fi Beat — Chill"]


def make(store: Store, seed: int = 7, n_reels: int = 140) -> None:
    rnd = random.Random(seed)
    codes = []
    for i in range(n_reels):
        acc = rnd.choice(ACCOUNTS)
        tags = rnd.sample(TAGS, rnd.randint(1, 4))
        likes = int(rnd.lognormvariate(8.2, 1.6))
        d = {"pk": str(7000 + i), "code": f"Sm{i:05d}x", "like_count": likes, "comment_count": int(likes * rnd.uniform(0.002, 0.03)),
             "play_count": int(likes * rnd.uniform(8, 40)), "reshare_count": int(likes * rnd.uniform(0.01, 0.1)),
             "send_count": int(likes * rnd.uniform(0.02, 0.2)), "video_duration": round(rnd.uniform(6, 45), 1),
             "user": {"username": acc}, "caption": {"text": f"{acc} paylaşımı {i} " + " ".join(tags)},
             "clips_metadata": {"music_info": {"music_asset_info": {"title": rnd.choice(MUSIC).split(" — ")[0], "display_artist": "X"}}}
             if rnd.random() < 0.5 else {"original_sound_info": {"original_audio_title": "özgün ses", "ig_artist": {"username": acc}}},
             "product_type": "clips", "video_versions": []}
        if rnd.random() < 0.09:
            d["ad_id"] = f"ad{i}"
        rec = extract.media_record(d)
        rec["music"] = rnd.choice(MUSIC)
        store.upsert_media(rec)
        codes.append((rec["code"], rec["duration"]))
    now = time.time() - 3 * 3600
    for scenario, count, (lo, hi) in (("fixed", 90, (3, 6)), ("skip", 70, (1, 2)), ("full", 60, (6, 40)), ("mix", 100, (1.5, 15))):
        sid = store.start_session(scenario, {"scenario": scenario}, "0.3.0", "Test")
        for seq in range(1, count + 1):
            code, dur = rnd.choice(codes)
            dwell = rnd.uniform(lo, hi)
            watched = min(dwell, dur * rnd.uniform(0.6, 1.6)) * rnd.uniform(0.7, 1.0)
            now += dwell + rnd.uniform(0.2, 1)
            store.add_view({"session_id": sid, "seq": seq, "code": code, "t_start": now - dwell, "t_end": now, "dwell": round(dwell, 2),
                            "watched": round(watched, 2), "duration": dur, "loops": int(watched > dur), "action": "otomatik",
                            "starred": 1 if rnd.random() < 0.03 else 0, "note": "ilginç" if rnd.random() < 0.02 else ""})
        store.end_session(sid)
