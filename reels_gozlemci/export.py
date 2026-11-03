"""Verileri CSV ve JSON olarak dışa aktarır."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List

from .store import Store

VIEW_COLUMNS = ["session_id", "seq", "time", "code", "username", "action", "dwell", "watched", "duration",
                "watched_frac", "loops", "starred", "note", "is_ad", "like_count", "comment_count", "play_count",
                "reshare_count", "has_liked", "music", "hashtags", "caption", "taken_at"]
REEL_COLUMNS = ["code", "username", "full_name", "caption", "hashtags", "music", "like_count", "comment_count",
                "play_count", "reshare_count", "like_first", "comment_first", "play_first", "duration", "product_type",
                "taken_at", "is_ad", "has_liked", "likes_hidden", "location", "first_seen", "last_seen", "n_views",
                "total_dwell", "total_watched", "pk"]


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".") if v == v else ""
    if isinstance(v, str):
        return v.replace("\r", " ").replace("\n", " ")
    return v


def _write_csv(path: Path, columns: List[str], rows: List[Dict[str, Any]]) -> None:
    # Excel Türkçe sürümü için ; ayracı ve BOM'lu UTF-8
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(columns)
        for r in rows:
            w.writerow([_cell(r.get(c)) for c in columns])


def export_all(store: Store, out_dir: Path) -> Path:
    """<out_dir>/reels-<tarih>/ altına gorunumler.csv, reeller.csv ve veri.json yazar; klasörü döndürür."""
    folder = Path(out_dir) / ("reels-" + time.strftime("%Y%m%d-%H%M%S"))
    folder.mkdir(parents=True, exist_ok=True)
    views = store.export_views()
    reels = store.export_reels(with_raw=True)
    _write_csv(folder / "gorunumler.csv", VIEW_COLUMNS, views)
    _write_csv(folder / "reeller.csv", REEL_COLUMNS, reels)
    (folder / "veri.json").write_text(
        json.dumps({"gorunumler": views, "reeller": reels}, ensure_ascii=False, indent=1), encoding="utf-8")
    return folder
