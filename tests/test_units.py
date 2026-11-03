import csv
import json
import tempfile
import unittest
from pathlib import Path

from reels_gozlemci import export, extract, livewrite, paths, report, session
from reels_gozlemci.store import Store


def media(code="Abc12345", **kw):
    d = {"pk": "1", "code": code, "like_count": 10, "comment_count": 2, "play_count": 300,
         "video_duration": 7.5, "caption": {"text": "Selam #bir #İki"}, "user": {"username": "ali"},
         "clips_metadata": {"music_info": {"music_asset_info": {"title": "T", "display_artist": "A"}}},
         "video_versions": [{"url": "x" * 5000}]}
    d.update(kw)
    return d


class ExtractTests(unittest.TestCase):
    def test_nested_edges(self):
        text = json.dumps({"data": {"x": {"edges": [{"node": {"media": media("AAAAA1")}}, {"node": {"media": media("BBBBB2")}}]}}})
        recs = extract.extract_media(text)
        self.assertEqual({r["code"] for r in recs}, {"AAAAA1", "BBBBB2"})
        r = [x for x in recs if x["code"] == "AAAAA1"][0]
        self.assertEqual((r["username"], r["like_count"], r["comment_count"], r["play_count"]), ("ali", 10, 2, 300))
        self.assertEqual(r["duration"], 7.5)
        self.assertEqual(r["hashtags"], "#bir #İki")
        self.assertEqual(r["music"], "T — A")
        self.assertEqual(r["is_ad"], 0)

    def test_prefix_and_ndjson(self):
        one = json.dumps({"a": media("CCCCC3")})
        self.assertEqual(len(extract.extract_media("for (;;);" + one)), 1)
        nd = one + "\n" + json.dumps({"b": media("DDDDD4")}) + "\nçöp satır"
        self.assertEqual({r["code"] for r in extract.extract_media(nd)}, {"CCCCC3", "DDDDD4"})

    def test_ad_and_non_media(self):
        recs = extract.extract_media(json.dumps({"m": media("EEEEE5", ad_id="9")}))
        self.assertEqual(recs[0]["is_ad"], 1)
        self.assertEqual(extract.extract_media(json.dumps({"code": "OK", "x": 1})), [])
        self.assertEqual(extract.extract_media("{}"), [])
        self.assertEqual(extract.extract_media("kod yok"), [])

    def test_play_count_alternatives_and_raw_is_slim(self):
        recs = extract.extract_media(json.dumps({"m": media("FFFFF6", play_count=None, ig_play_count=777)}))
        self.assertEqual(recs[0]["play_count"], 777)
        import zlib
        raw = zlib.decompress(recs[0]["raw"]).decode("utf-8")
        self.assertNotIn("video_versions", raw)
        self.assertIn("clips_metadata", raw)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def rec(self, code="Abc12345", likes=10):
        return extract.media_record(media(code, like_count=likes))

    def test_upsert_keeps_first_counts(self):
        self.assertTrue(self.store.upsert_media(self.rec(likes=10)))
        self.assertFalse(self.store.upsert_media(self.rec(likes=25)))
        row = self.store.export_reels()[0]
        self.assertEqual((row["like_count"], row["like_first"]), (25, 10))
        self.assertEqual(self.store.count_reels(), 1)

    def test_views_and_export(self):
        self.store.upsert_media(self.rec())
        sid = self.store.start_session("fixed", {"a": 1}, "0.1.0", "Test")
        self.store.add_view({"session_id": sid, "seq": 1, "code": "Abc12345", "t_start": 1.0, "t_end": 5.0, "dwell": 4.0,
                             "watched": 3.0, "duration": 6.0, "loops": 0, "action": "otomatik", "starred": 1, "note": "n"})
        views = self.store.export_views()
        self.assertEqual(views[0]["watched_frac"], 0.5)
        self.assertEqual(views[0]["username"], "ali")
        reel = self.store.export_reels()[0]
        self.assertEqual((reel["n_views"], reel["total_dwell"]), (1, 4.0))
        out = export.export_all(self.store, Path(self.tmp.name) / "out")
        for name in ("gorunumler.csv", "reeller.csv", "veri.json"):
            self.assertTrue((out / name).is_file(), name)
        with open(out / "gorunumler.csv", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f, delimiter=";"))
        self.assertEqual(rows[0]["username"], "ali")
        self.assertEqual(rows[0]["watched_frac"], "0.5")
        data = json.loads((out / "veri.json").read_text(encoding="utf-8"))
        self.assertEqual(data["reeller"][0]["raw"]["code"], "Abc12345")


class SettingsAndReportTests(unittest.TestCase):
    def test_clean_settings(self):
        s = paths.clean_settings({"scenario": "kötü", "min_s": 10, "max_s": 2, "limit": -5, "start_url": "ftp://x", "zz": 1})
        self.assertEqual(s["scenario"], "fixed")
        self.assertEqual((s["min_s"], s["max_s"]), (2.0, 10.0))
        self.assertEqual(s["limit"], 0)
        self.assertTrue(s["start_url"].startswith("https://"))
        self.assertNotIn("zz", s)

    def test_report_redacts_secrets(self):
        snap = {"phase": "calisiyor", "logs": [{"t": "12:00:00", "level": "info", "text": "sessionid=ABC123 ve csrftoken: XYZ"}]}
        text = report.build(paths.clean_settings({}), snap)
        self.assertNotIn("ABC123", text)
        self.assertNotIn("XYZ", text)
        self.assertIn("Reels Gözlemci raporu", text)


class NewFieldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_counts_found_in_nested_and_alt_names(self):
        d = media("NEST123", play_count=None, clips_metadata={"ig_play_count": 4321, "repost_count": 7},
                  send_count=12)
        rec = extract.media_record(d)
        self.assertEqual((rec["play_count"], rec["reshare_count"], rec["send_count"]), (4321, 7, 12))

    def test_send_count_stored_and_exported(self):
        self.store.upsert_media(extract.media_record(media("SEND123", send_count=9, reshare_count=3)))
        reel = self.store.get_reel("SEND123")
        self.assertEqual((reel["send_count"], reel["reshare_count"]), (9, 3))
        self.assertIn("send_count", export.VIEW_COLUMNS)
        self.assertIn("send_count", export.REEL_COLUMNS)

    def test_duration_fallback_only_when_missing(self):
        self.store.upsert_media(extract.media_record(media("DUR1234", video_duration=None)))
        self.store.set_duration_if_missing("DUR1234", 12.5)
        self.assertEqual(self.store.get_reel("DUR1234")["duration"], 12.5)
        self.store.set_duration_if_missing("DUR1234", 99.0)
        self.assertEqual(self.store.get_reel("DUR1234")["duration"], 12.5)
        self.store.upsert_media(extract.media_record(media("DUR5678", video_duration=7.0)))
        self.store.set_duration_if_missing("DUR5678", 99.0)
        self.assertEqual(self.store.get_reel("DUR5678")["duration"], 7.0)

    def test_numeric_fields_summary_lists_counter_paths(self):
        self.store.upsert_media(extract.media_record(media("FLD1234", ig_play_count=55, clips_metadata={"share_count": 4, "x": 1})))
        lines = self.store.numeric_fields_summary()
        text = "\n".join(lines)
        self.assertIn("ig_play_count", text)
        self.assertIn("clips_metadata.share_count", text)
        self.assertNotIn("clips_metadata.x:", text)

    def test_old_database_is_upgraded(self):
        import sqlite3
        path = Path(self.tmp.name) / "old.sqlite3"
        db = sqlite3.connect(str(path))
        db.executescript("CREATE TABLE reels(code TEXT PRIMARY KEY, pk TEXT, username TEXT, full_name TEXT, caption TEXT, hashtags TEXT,"
                         " like_count INTEGER, comment_count INTEGER, play_count INTEGER, reshare_count INTEGER, like_first INTEGER,"
                         " comment_first INTEGER, play_first INTEGER, duration REAL, music TEXT, product_type TEXT, taken_at INTEGER,"
                         " is_ad INTEGER, has_liked INTEGER, likes_hidden INTEGER, location TEXT, first_seen REAL, last_seen REAL, raw BLOB);")
        db.commit()
        db.close()
        st = Store(path)
        try:
            self.assertTrue(st.upsert_media(extract.media_record(media("UPG1234", send_count=5))))
            self.assertEqual(st.get_reel("UPG1234")["send_count"], 5)
        finally:
            st.close()


class FolderAndLiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "t.sqlite3")
        self.store.upsert_media(extract.media_record(media("LIVE123")))
        self.sid = self.store.start_session("fixed", {}, "0.3.0", "T")
        self.logs = []

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def add_view(self, seq):
        self.store.add_view({"session_id": self.sid, "seq": seq, "code": "LIVE123", "t_start": 1.0, "t_end": 5.0, "dwell": 4.0,
                             "watched": 3.0, "duration": 6.0, "loops": 0, "action": "otomatik", "starred": 0, "note": ""})

    def test_check_writable(self):
        self.assertIsNone(paths.check_writable(self.root / "yeni" / "alt"))
        blocker = self.root / "dosya.txt"
        blocker.write_text("x")
        err = paths.check_writable(blocker / "alt")
        self.assertTrue(err and ("Error" in err))

    def test_default_dirs_are_paths(self):
        self.assertEqual(paths.default_export_dir().name, "Reels Gozlemci")
        self.assertTrue(str(paths.fallback_export_dir()))

    def test_live_writer_appends_and_headers_once(self):
        lw = livewrite.LiveWriter(self.store, lambda: self.root / "canli", lambda l, t: self.logs.append((l, t)))
        self.add_view(1)
        lw.on_view()
        self.add_view(2)
        lw.on_view()
        lw.on_view()                       # yeni kayıt yok: tekrar yazmaz
        import csv
        with open(self.root / "canli" / livewrite.FILE_NAME, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f, delimiter=";"))
        self.assertEqual(rows[0][:3], ["session_id", "scenario", "seq"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][2], "1")
        self.assertEqual(rows[2][2], "2")

    def test_live_writer_retries_when_file_locked(self):
        lw = livewrite.LiveWriter(self.store, lambda: self.root / "canli", lambda l, t: self.logs.append((l, t)))
        self.add_view(1)
        lw.on_view()
        path = self.root / "canli" / livewrite.FILE_NAME
        real_open = open
        import builtins
        from unittest import mock

        def locked(file, *a, **k):
            if str(file) == str(path):
                raise PermissionError(13, "Permission denied")
            return real_open(file, *a, **k)
        self.add_view(2)
        with mock.patch.object(builtins, "open", locked):
            lw.on_view()
        self.assertTrue(lw.error)
        self.assertTrue(any(l == "warn" for l, _ in self.logs))
        lw.on_view()                       # kilit kalktı: bekleyen kayıt yazılır
        self.assertEqual(lw.error, "")
        with open(path, encoding="utf-8-sig") as f:
            self.assertEqual(len(f.read().strip().splitlines()), 3)

    def test_live_writer_new_folder_gets_all_rows(self):
        target = {"d": self.root / "a"}
        lw = livewrite.LiveWriter(self.store, lambda: target["d"], lambda l, t: None)
        self.add_view(1)
        lw.on_view()
        target["d"] = self.root / "b"
        self.add_view(2)
        lw.on_view()
        with open(self.root / "b" / livewrite.FILE_NAME, encoding="utf-8-sig") as f:
            self.assertEqual(len(f.read().strip().splitlines()), 3)   # başlık + 2 kayıt

    def test_live_writer_disabled(self):
        lw = livewrite.LiveWriter(self.store, lambda: None, lambda l, t: None)
        self.add_view(1)
        lw.on_view()
        self.assertFalse((self.root / "canli").exists())


class SessionTests(unittest.TestCase):
    SID = "12345678%3Aabcdefghij%3A5%3AAYdxyz"

    def test_bare_sessionid(self):
        got = session.parse_session_input(self.SID)
        self.assertEqual(got["sessionid"], self.SID)
        self.assertEqual(got["ds_user_id"], "12345678")

    def test_colon_form_is_encoded(self):
        got = session.parse_session_input("sessionid=12345678:abcdefghij:5:AYdxyz")
        self.assertEqual(got["sessionid"], self.SID)

    def test_cookie_line_and_device_cookies(self):
        line = f"Cookie: sessionid={self.SID}; csrftoken=Tok123456; mid=MIDVAL; ig_did=AAAA-BBBB; datr=DATRVAL; foo=bar"
        got = session.parse_session_input(line)
        self.assertEqual(set(got), {"sessionid", "csrftoken", "mid", "ig_did", "datr", "ds_user_id"})
        self.assertNotIn("foo", got)

    def test_devtools_tab_format(self):
        got = session.parse_session_input(f"sessionid\t{self.SID}\t.instagram.com\t/")
        self.assertEqual(got["sessionid"], self.SID)

    def test_invalid(self):
        for bad in ("", "   ", "kısa", "a b c", "sessionid=", "x=y"):
            self.assertEqual(session.parse_session_input(bad), {}, bad)

    def test_clean_cookies_from_extension(self):
        got = session.clean_cookies({"sessionid": self.SID, "mid": "M1", "evil": "x", "csrftoken": 5})
        self.assertEqual(set(got), {"sessionid", "mid", "ds_user_id"})
        self.assertEqual(session.clean_cookies("yok"), {})

    def test_cookie_items_domains(self):
        items = session.cookie_items({"sessionid": self.SID}, "https://www.instagram.com/reels/")
        self.assertEqual((items[0]["domain"], items[0]["secure"], items[0]["httpOnly"]), (".instagram.com", True, True))
        items = session.cookie_items({"sessionid": self.SID}, "http://127.0.0.1:8000/reels/")
        self.assertEqual((items[0]["domain"], items[0]["secure"]), ("127.0.0.1", False))


if __name__ == "__main__":
    unittest.main()
