import csv
import json
import tempfile
import unittest
from pathlib import Path

from reels_gozlemci import export, extract, paths, report
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


if __name__ == "__main__":
    unittest.main()
