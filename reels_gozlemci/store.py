"""Veri tabanı (SQLite): Reel bilgileri, izleme kayıtları ve oturumlar."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, ended TEXT, scenario TEXT, settings TEXT,
  app_version TEXT, browser TEXT);
CREATE TABLE IF NOT EXISTS reels(
  code TEXT PRIMARY KEY, pk TEXT, username TEXT, full_name TEXT, caption TEXT, hashtags TEXT,
  like_count INTEGER, comment_count INTEGER, play_count INTEGER, reshare_count INTEGER, send_count INTEGER,
  like_first INTEGER, comment_first INTEGER, play_first INTEGER,
  duration REAL, music TEXT, product_type TEXT, taken_at INTEGER, is_ad INTEGER, has_liked INTEGER,
  likes_hidden INTEGER, location TEXT, first_seen REAL, last_seen REAL, raw BLOB);
CREATE TABLE IF NOT EXISTS views(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, seq INTEGER, code TEXT,
  t_start REAL, t_end REAL, dwell REAL, watched REAL, duration REAL, loops INTEGER,
  action TEXT, starred INTEGER, note TEXT);
CREATE INDEX IF NOT EXISTS views_code ON views(code);
CREATE INDEX IF NOT EXISTS views_session ON views(session_id);
"""

_UPDATE_FIELDS = ("pk", "username", "full_name", "caption", "hashtags", "like_count", "comment_count",
                  "play_count", "reshare_count", "send_count", "duration", "music", "product_type", "taken_at",
                  "has_liked", "location")


def iso(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts if ts is not None else time.time()))


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.executescript(SCHEMA)
            cols = {r["name"] for r in self.db.execute("PRAGMA table_info(reels)")}
            if "send_count" not in cols:                      # 0.2.x veritabanını yükselt
                self.db.execute("ALTER TABLE reels ADD COLUMN send_count INTEGER")
            self.db.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self.db.close()
            except sqlite3.Error:
                pass

    # ---------------------------------------------------------------- oturum
    def start_session(self, scenario: str, settings: Dict[str, Any], version: str, browser: str) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO sessions(started, scenario, settings, app_version, browser) VALUES(?,?,?,?,?)",
                (iso(), scenario, json.dumps(settings, ensure_ascii=False), version, browser))
            self.db.commit()
            return int(cur.lastrowid)

    def end_session(self, session_id: int) -> None:
        with self._lock:
            self.db.execute("UPDATE sessions SET ended=? WHERE id=?", (iso(), session_id))
            self.db.commit()

    # ---------------------------------------------------------------- reeller
    def upsert_media(self, rec: Dict[str, Any]) -> bool:
        """Kaydı ekler/günceller. Yeni bir Reel ise True döner."""
        now = time.time()
        with self._lock:
            row = self.db.execute("SELECT code, raw FROM reels WHERE code=?", (rec["code"],)).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO reels(code, pk, username, full_name, caption, hashtags, like_count, comment_count,"
                    " play_count, reshare_count, send_count, like_first, comment_first, play_first, duration, music,"
                    " product_type, taken_at, is_ad, has_liked, likes_hidden, location, first_seen, last_seen, raw)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rec["code"], rec["pk"], rec["username"], rec["full_name"], rec["caption"], rec["hashtags"],
                     rec["like_count"], rec["comment_count"], rec["play_count"], rec["reshare_count"], rec.get("send_count"),
                     rec["like_count"], rec["comment_count"], rec["play_count"], rec["duration"], rec["music"],
                     rec["product_type"], rec["taken_at"], rec["is_ad"], rec["has_liked"], rec["likes_hidden"],
                     rec["location"], now, now, rec["raw"]))
                self.db.commit()
                return True
            sets, vals = ["last_seen=?"], [now]
            for f in _UPDATE_FIELDS:
                if rec.get(f) not in (None, ""):
                    sets.append(f"{f}=?")
                    vals.append(rec[f])
            if rec.get("is_ad"):
                sets.append("is_ad=1")
            if len(rec["raw"]) > len(row["raw"] or b""):
                sets.append("raw=?")
                vals.append(rec["raw"])
            vals.append(rec["code"])
            self.db.execute(f"UPDATE reels SET {', '.join(sets)} WHERE code=?", vals)
            self.db.commit()
            return False

    def get_reel(self, code: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self.db.execute(
                "SELECT code, username, caption, hashtags, like_count, comment_count, play_count, reshare_count,"
                " send_count, duration, music, is_ad, has_liked, taken_at FROM reels WHERE code=?", (code,)).fetchone()
        return dict(row) if row else None

    def set_duration_if_missing(self, code: str, duration: float) -> None:
        """Instagram yanıtında süre yoksa videonun kendi süresini kaydeder."""
        with self._lock:
            self.db.execute("UPDATE reels SET duration=? WHERE code=? AND (duration IS NULL OR duration<=0)",
                            (float(duration), code))
            self.db.commit()

    def numeric_fields_summary(self, limit_rows: int = 400, max_lines: int = 90) -> List[str]:
        """Ham kayıtlardaki sayaç benzeri alanlar: "yol: görülme sayısı, örnek değerler". İzlenme/paylaşım/gönderme
        sayılarının Instagram'daki gerçek alan adını bulmak için rapora yazılır."""
        from . import extract
        with self._lock:
            rows = self.db.execute("SELECT raw FROM reels ORDER BY last_seen DESC LIMIT ?", (limit_rows,)).fetchall()
        seen: Dict[str, List[Any]] = {}
        for r in rows:
            try:
                doc = json.loads(zlib.decompress(r["raw"]).decode("utf-8"))
            except Exception:
                continue
            for path, value in extract.numeric_fields(doc):
                seen.setdefault(path, []).append(value)
        lines = []
        for path, vals in sorted(seen.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:max_lines]:
            ex = ", ".join(str(v) for v in vals[:3])
            lines.append(f"{path}: {len(vals)}/{len(rows)} Reel'de, örnek: {ex}")
        return lines

    def count_reels(self) -> int:
        with self._lock:
            return int(self.db.execute("SELECT COUNT(*) FROM reels").fetchone()[0])

    # ---------------------------------------------------------------- izlemeler
    def add_view(self, v: Dict[str, Any]) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO views(session_id, seq, code, t_start, t_end, dwell, watched, duration, loops, action,"
                " starred, note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (v["session_id"], v["seq"], v["code"], v["t_start"], v["t_end"], v["dwell"], v["watched"],
                 v["duration"], v["loops"], v["action"], v["starred"], v["note"]))
            self.db.commit()
            return int(cur.lastrowid)

    def recent_views(self, session_id: int, n: int = 8) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.db.execute(
                "SELECT v.seq, v.code, v.dwell, v.watched, v.duration, v.action, v.starred, r.username, r.is_ad"
                " FROM views v LEFT JOIN reels r ON r.code=v.code WHERE v.session_id=? ORDER BY v.id DESC LIMIT ?",
                (session_id, n)).fetchall()
        return [dict(r) for r in rows]

    def sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.db.execute(
                "SELECT s.id, s.started, s.ended, s.scenario, s.browser, (SELECT COUNT(*) FROM views v WHERE v.session_id=s.id) AS n"
                " FROM sessions s ORDER BY s.id").fetchall()
        return [dict(r) for r in rows]

    def views_total(self) -> int:
        with self._lock:
            return int(self.db.execute("SELECT COUNT(*) FROM views").fetchone()[0])

    # ---------------------------------------------------------------- dışa aktarma
    def export_views(self, after_id: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.db.execute(
                "SELECT v.id, v.session_id, s.scenario, v.seq, v.t_start, v.t_end, v.dwell, v.watched, v.duration,"
                " v.loops, v.action, v.starred, v.note, v.code, r.username, r.caption, r.hashtags, r.music,"
                " r.like_count, r.comment_count, r.play_count, r.reshare_count, r.send_count, r.is_ad, r.has_liked,"
                " r.taken_at FROM views v LEFT JOIN reels r ON r.code=v.code"
                " LEFT JOIN sessions s ON s.id=v.session_id WHERE v.id>? ORDER BY v.id", (after_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["time"] = iso(d["t_start"]) if d.get("t_start") else ""
            d["watched_frac"] = (round(d["watched"] / d["duration"], 3)
                                 if d.get("duration") and d.get("watched") is not None else None)
            out.append(d)
        return out

    def export_reels(self, with_raw: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.db.execute(
                "SELECT r.*, (SELECT COUNT(*) FROM views v WHERE v.code=r.code) AS n_views,"
                " (SELECT SUM(dwell) FROM views v WHERE v.code=r.code) AS total_dwell,"
                " (SELECT SUM(watched) FROM views v WHERE v.code=r.code) AS total_watched"
                " FROM reels r ORDER BY r.first_seen").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            raw = d.pop("raw", None)
            if with_raw and raw:
                try:
                    d["raw"] = json.loads(zlib.decompress(raw).decode("utf-8"))
                except Exception:
                    d["raw"] = None
            d["first_seen"] = iso(d["first_seen"]) if d.get("first_seen") else ""
            d["last_seen"] = iso(d["last_seen"]) if d.get("last_seen") else ""
            out.append(d)
        return out
