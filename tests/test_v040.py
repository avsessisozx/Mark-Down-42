"""0.4.0 testleri: ekran sayaçları, hesap verisi paketi, Profilim, oranlar, izleme listesi."""

import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path

os.environ.setdefault("REELS_GOZLEMCI_BROWSER", "chromium")
os.environ.setdefault("REELS_GOZLEMCI_HEADLESS", "1")
os.environ["REELS_GOZLEMCI_NO_OPEN"] = "1"

from reels_gozlemci import counts, hesap_verisi, oranlar, profil
from reels_gozlemci.store import Store
from tests.test_integration import EngineBase, wait_for


def rec(code, user="ali", **kw):
    d = {"code": code, "pk": code, "username": user, "full_name": "", "caption": kw.pop("caption", ""), "hashtags": "",
         "like_count": None, "comment_count": None, "play_count": None, "reshare_count": None, "send_count": None,
         "follower_count": None, "duration": 5.0, "music": "", "product_type": "clips", "taken_at": None, "is_ad": 0,
         "has_liked": None, "likes_hidden": 0, "location": "", "raw": b"x"}
    d.update(kw)
    return d


class CountsTests(unittest.TestCase):
    def test_parse(self):
        cases = {"87": 87, "1.234": 1234, "1,234": 1234, "1,2 B": 1200, "12.4K": 12400, "3 Mn": 3_000_000,
                 "2,5 Mn": 2_500_000, "1.2M": 1_200_000, "15 B": 15000, "1 234": 1234}
        for text, want in cases.items():
            self.assertEqual(counts.parse_count(text), want, text)
        for bad in ("", None, "abc", "1.2.3 B", "12 zz", "-"):
            self.assertIsNone(counts.parse_count(bad), bad)

    def test_labels(self):
        self.assertEqual(counts.classify_label("Beğen"), "like_count")
        self.assertEqual(counts.classify_label("Like"), "like_count")
        self.assertEqual(counts.classify_label("Yorum"), "comment_count")
        self.assertEqual(counts.classify_label("Yeniden paylaş"), "reshare_count")
        self.assertEqual(counts.classify_label("Paylaş"), "send_count")
        self.assertEqual(counts.classify_label("İzlenme"), "play_count")
        self.assertEqual(counts.classify_label("izlenme"), "play_count")
        self.assertIsNone(counts.classify_label("Kaydet"))
        self.assertIsNone(counts.classify_label(""))

    def test_fields(self):
        cands = [{"label": "Beğen", "text": "1,1 B"}, {"label": "", "text": "99"}, {"label": "Paylaş", "text": "7"},
                 {"label": "Beğen", "text": "5"}, {"label": "Yorum", "text": "x"}]
        self.assertEqual(counts.fields_from_candidates(cands), {"like_count": 1100, "send_count": 7})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rg-t040-"))
        self.store = Store(self.tmp / "t.sqlite3")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_screen_fill_never_overwrites_api(self):
        self.store.upsert_media(rec("AAAAA1", like_count=10))
        written = self.store.fill_screen_counts("AAAAA1", {"like_count": 999, "play_count": 500})
        self.assertEqual(written, ["play_count"])
        row = self.store.get_reel("AAAAA1")
        self.assertEqual((row["like_count"], row["play_count"]), (10, 500))
        self.assertEqual(self.store.export_reels()[0]["counts_src"], "play_count")
        self.assertEqual(self.store.fill_screen_counts("AAAAA1", {"play_count": 600}), [])

    def test_follower_count_saved(self):
        self.store.upsert_media(rec("AAAAA2", follower_count=1234))
        self.assertEqual(self.store.export_reels()[0]["follower_count"], 1234)

    def test_watchlist_snapshots(self):
        self.store.upsert_media(rec("AAAAA3", play_count=100))
        self.assertTrue(self.store.watch_add("AAAAA3"))
        self.assertFalse(self.store.watch_add("AAAAA3"))
        self.assertEqual(len(self.store.watch_overview()[0]["snapshots"]), 1)
        self.store.upsert_media(rec("AAAAA3", play_count=150))       # 10 dk dolmadı: yeni ölçüm yok
        self.assertEqual(len(self.store.watch_overview()[0]["snapshots"]), 1)
        self.assertTrue(self.store.snapshot_now("AAAAA3"))
        snaps = self.store.watch_overview()[0]["snapshots"]
        self.assertEqual([s["play_count"] for s in snaps], [100, 150])
        self.store.watch_remove("AAAAA3")
        self.assertEqual(self.store.watch_overview(), [])

    def test_snapshot_without_numbers_skipped(self):
        self.store.upsert_media(rec("AAAAA4"))
        self.store.watch_add("AAAAA4")
        self.assertEqual(self.store.watch_overview()[0]["snapshots"], [])


def make_package(root: Path) -> None:
    def w(rel, obj):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    w("connections/followers_and_following/following.json", {"relationships_following": [
        {"title": "ali", "string_list_data": [{"href": "https://www.instagram.com/ali", "timestamp": 1700000000}]},
        {"title": "veli", "string_list_data": [{"href": "https://www.instagram.com/veli", "timestamp": 1700000001}]}]})
    w("your_instagram_activity/likes/liked_posts.json", {"likes_media_likes": [
        {"title": "ayse", "string_list_data": [{"href": "https://www.instagram.com/reel/Zz11111/", "value": "", "timestamp": 1700000100}]},
        {"title": "ayse", "string_list_data": [{"href": "https://www.instagram.com/p/Zz22222/", "timestamp": 1700000200}]}]})
    w("your_instagram_activity/saved/saved_posts.json", {"saved_saved_media": [
        {"title": "can", "string_map_data": {"Saved on": {"href": "https://www.instagram.com/reel/Zz33333/", "timestamp": 1700000300}}}]})
    # Instagram'ın bozuk UTF-8 kodlaması: "çok güzel #yemek" -> latin-1 olarak yazılmış
    bad = "çok güzel #yemek".encode("utf-8").decode("latin-1")
    w("your_instagram_activity/comments/post_comments_1.json", [
        {"string_map_data": {"Comment": {"value": bad}, "Media Owner": {"value": "deniz"}, "Time": {"timestamp": 1700000400}}}])
    w("preferences/your_topics/your_topics.json", {"topics_your_topics": [
        {"string_map_data": {"Name": {"value": "Motosiklet"}}}, {"string_map_data": {"Name": {"value": "Yemek"}}}]})
    w("ads_information/ads_and_topics/videos_watched.json", {"impressions_history_videos_watched": [
        {"string_map_data": {"Author": {"value": "ayse"}, "Time": {"timestamp": 1700000500}}}]})
    w("random/unknown_file.json", {"x": [{"title": "t"}]})
    # DM'ler okunmamalı
    w("your_instagram_activity/messages/inbox/biri_123/message_1.json", {"messages": [
        {"title": "gizli_kisi", "string_map_data": {"Comment": {"value": "ÖZEL MESAJ"}}}]})


class HesapVerisiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rg-t040-"))
        self.store = Store(self.tmp / "t.sqlite3")
        self.pkg = self.tmp / "paket"
        make_package(self.pkg)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, summary):
        cats = summary["categories"]
        self.assertEqual(cats.get("takip"), 2)
        self.assertEqual(cats.get("begeni"), 2)
        self.assertEqual(cats.get("kayit"), 1)
        self.assertEqual(cats.get("yorum"), 1)
        self.assertEqual(cats.get("ilgi_alani"), 2)
        self.assertEqual(cats.get("izlenen_video"), 1)
        self.assertIn("random/unknown_file.json", summary["unrecognized"])
        rows = self.store.account_rows()
        self.assertFalse(any("gizli_kisi" in (r["username"] or "") or "ÖZEL" in (r["text"] or "") for r in rows), "DM okunmamalı")
        liked = [r for r in rows if r["category"] == "begeni"]
        self.assertEqual({r["code"] for r in liked}, {"Zz11111", "Zz22222"})
        self.assertEqual({r["username"] for r in liked}, {"ayse"})
        comment = [r for r in rows if r["category"] == "yorum"][0]
        self.assertEqual(comment["text"], "çok güzel #yemek")        # bozuk kodlama düzeltildi
        self.assertEqual(comment["username"], "deniz")
        self.assertEqual({r["text"] for r in rows if r["category"] == "ilgi_alani"}, {"Motosiklet", "Yemek"})
        self.assertEqual([r for r in rows if r["category"] == "kayit"][0]["code"], "Zz33333")

    def test_folder(self):
        self.check(hesap_verisi.import_package(self.pkg, self.store))

    def test_zip(self):
        z = self.tmp / "paket.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for f in self.pkg.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(self.pkg).as_posix())
        self.check(hesap_verisi.import_package(z, self.store))

    def test_reimport_replaces(self):
        hesap_verisi.import_package(self.pkg, self.store)
        n1 = len(self.store.account_rows())
        hesap_verisi.import_package(self.pkg, self.store)
        self.assertEqual(len(self.store.account_rows()), n1)

    def test_html_only_gives_clear_error(self):
        h = self.tmp / "html"
        (h / "x").mkdir(parents=True)
        (h / "x" / "a.html").write_text("<html></html>")
        s = hesap_verisi.import_package(h, self.store)
        self.assertEqual(s["items"], 0)
        self.assertIn("JSON", s["errors"][0])


class ProfilTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rg-t040-"))
        self.store = Store(self.tmp / "t.sqlite3")
        make_package(self.tmp / "paket")
        hesap_verisi.import_package(self.tmp / "paket", self.store)
        for i, u in enumerate(["ali", "ayse", "ayse", "kimse", "kimse", "kimse"]):
            self.store.upsert_media(rec(f"R{i:05d}x", u, caption=f"x #tag{i % 2}", music="Şarkı" if i % 2 else ""))
        self.store.upsert_media(rec("ADxxxxx1", "reklamci", is_ad=1))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_profile(self):
        p = profil.build_profile(self.store)
        self.assertTrue(p["has_account_data"] and p["has_feed"])
        f = p["feed"]
        self.assertEqual((f["reels"], f["ads"]), (7, 1))
        self.assertAlmostEqual(f["share_following"], 1 / 6, places=2)           # yalnız ali takip ediliyor
        self.assertAlmostEqual(f["share_liked_before"], 2 / 6, places=2)         # ayse 2 Reel
        self.assertAlmostEqual(f["share_unexplained"], 3 / 6, places=2)          # kimse
        self.assertEqual(p["you"]["following"], 2)
        self.assertEqual(p["you"]["top_engaged"][0]["label"], "@ayse")
        self.assertEqual(p["instagram_labels"]["topics"][0], "Motosiklet")
        by_user = {r["username"]: r for r in p["reasons"]}
        self.assertIn("Takip ediyorsun", by_user["ali"]["why"])
        self.assertTrue(any("beğenmişsin" in w for w in by_user["ayse"]["why"]))
        self.assertFalse(by_user["kimse"]["explained"])
        self.assertIn("Reklam", by_user["reklamci"]["why"])
        self.assertTrue(p["caveats"])

    def test_without_account_data(self):
        self.store.account_clear()
        p = profil.build_profile(self.store)
        self.assertFalse(p["has_account_data"])
        self.assertTrue(p["has_feed"])
        self.assertNotIn("share_following", p["feed"])
        self.assertEqual(p["reasons"], [])


class OranlarTests(unittest.TestCase):
    def test_outliers(self):
        now = 1_800_000_000
        reels = []
        for i in range(20):
            reels.append({"code": f"N{i:05d}x", "username": f"u{i}", "caption": "", "play_count": 1000 + i * 10, "like_count": 50,
                          "comment_count": 2, "send_count": 1, "reshare_count": 0, "follower_count": 5000, "taken_at": now - 86400,
                          "is_ad": 0, "n_views": 1})
        reels.append({"code": "VIRAL001", "username": "kucuk", "caption": "komik", "play_count": 2_000_000, "like_count": 90000,
                      "comment_count": 100, "send_count": 5, "reshare_count": 0, "follower_count": 800, "taken_at": now - 3600, "is_ad": 0, "n_views": 1})
        reels.append({"code": "REKLAM01", "username": "r", "caption": "", "play_count": 9_000_000, "follower_count": 10, "taken_at": now, "is_ad": 1, "n_views": 1})
        out = oranlar.analyze(reels, now)
        over = out["overperformers"]
        self.assertEqual(over[0]["code"], "VIRAL001")
        self.assertTrue(any("katı" in f for f in over[0]["flags"]))
        self.assertNotIn("REKLAM01", [o["code"] for o in over])      # reklam dışarıda
        self.assertEqual(out["reels"], 21)
        self.assertEqual(out["notes"], [])

    def test_no_followers_and_few_data_notes(self):
        out = oranlar.analyze([{"code": "AAAAA1", "play_count": 10, "is_ad": 0, "follower_count": None}])
        self.assertEqual(out["overperformers"], [])
        self.assertEqual(len(out["notes"]), 2)


class ScreenCountsEngineTests(EngineBase):
    def test_screen_counts_fill_missing_api_fields(self):
        self.run_until_done(self.settings(limit=6, min_s=1.0, max_s=1.2, start_url=self.base + "/reels/?counts=1&noplays=1"))
        reels = {r["code"]: r for r in self.store.export_reels()}
        done = [c for c in (fake.code_of(i) for i in range(5)) if c in reels]
        self.assertTrue(done)
        ok = 0
        for c in done:
            i = int(c[2:7])
            row = reels[c]
            if row["play_count"] is None:
                continue
            self.assertIn("play_count", row["counts_src"] or "")
            want = 1000 + i * 53
            self.assertLess(abs(row["play_count"] - want), want * 0.06, (c, row["play_count"], want))   # "1,1 B" ≈ yaklaşık
            self.assertEqual(row["send_count"], i * 3, c)                                                  # küçük sayı tam
            self.assertEqual(row["like_count"], 100 + i * 7)                                                # API'den, ekran ezmedi
            ok += 1
        self.assertGreaterEqual(ok, 3, [(c, reels[c]["play_count"]) for c in done])
        snap = self.engine.snapshot()
        self.assertGreater(snap["screen_filled"], 0)
        self.assertTrue(snap["screen_samples"])


class RecheckEngineTests(EngineBase):
    def test_recheck_adds_snapshots(self):
        s = self.settings(auto=False, limit=0)
        self.assertTrue(self.engine.start(s))
        self.assertTrue(wait_for(lambda: self.store.count_reels() >= 10, 40), self.engine.snapshot()["logs"][-6:])
        codes = [fake.code_of(i) for i in range(3)]
        for c in codes:
            self.assertTrue(self.store.watch_add(c))
        self.engine.command("recheck")
        self.assertTrue(wait_for(lambda: all(len(w["snapshots"]) >= 2 for w in self.store.watch_overview()), 60),
                        [(w["code"], len(w["snapshots"])) for w in self.store.watch_overview()])
        self.assertTrue(wait_for(lambda: not self.engine.rechecking, 30))
        self.engine.stop()
        self.engine.wait(25)

    def test_star_adds_to_watchlist(self):
        self.assertTrue(self.engine.start(self.settings(auto=False, limit=0)))
        self.assertTrue(wait_for(lambda: self.engine.current_code != "" and self.engine.current.get("known"), 40),
                        self.engine.snapshot()["logs"][-6:])
        code = self.engine.current_code
        self.engine.command("star")
        self.assertTrue(wait_for(lambda: code in self.store.watch_codes(), 10))
        self.engine.stop()
        self.engine.wait(25)


from tests import fake_instagram as fake   # noqa: E402  (EngineBase testleri yukarıda kullanıyor)


class ServerTests(unittest.TestCase):
    def setUp(self):
        from reels_gozlemci.server import AppState, start_server
        self.tmp = Path(tempfile.mkdtemp(prefix="rg-t040-"))
        self.state = AppState(self.tmp)
        self.srv = start_server(self.state)
        self.base = f"http://127.0.0.1:{self.state.port}"
        make_package(self.tmp / "paket")

    def tearDown(self):
        self.srv.shutdown()
        self.state.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read().decode("utf-8")

    def post(self, path, body, token=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Token": token or self.state.token})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_pages_and_import(self):
        for page in ("/profil", "/analiz", "/"):
            st, html = self.get(page)
            self.assertEqual(st, 200)
            self.assertNotIn("__TOKEN__", html)
        self.state.store.upsert_media(rec("SRV0001x", "ali", play_count=900, like_count=10))
        code, r = self.post("/api/account-import", {"path": str(self.tmp / "paket")})
        self.assertEqual(code, 200)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["summary"]["categories"]["takip"], 2)
        st, body = self.get("/api/profile")
        p = json.loads(body)
        self.assertTrue(p["has_account_data"])
        self.assertIn("Takip ediyorsun", p["reasons"][0]["why"])
        st, body = self.get("/api/ratios")
        self.assertEqual(json.loads(body)["reels"], 1)
        code, r = self.post("/api/watch-remove", {"code": "yok"})
        self.assertEqual(code, 200)
        code, r = self.post("/api/recheck", {})
        self.assertEqual(code, 400)                      # program çalışmıyor
        self.assertIn("Başlat", r["error"])
        st, body = self.get("/api/report")
        self.assertIn("Hesap verisi", json.loads(body)["text"])

    def test_import_errors_and_token(self):
        code, r = self.post("/api/account-import", {"path": str(self.tmp / "yok")})
        self.assertEqual(code, 400)
        code, r = self.post("/api/account-import", {"path": str(self.tmp)}, token="yanlis")
        self.assertEqual(code, 403)
        (self.tmp / "bos").mkdir()
        code, r = self.post("/api/account-import", {"path": str(self.tmp / "bos")})
        self.assertFalse(r["ok"])
        self.assertIn("JSON", r["error"])

    def test_pick_via_test_env(self):
        os.environ["REELS_GOZLEMCI_TEST_PICK"] = str(self.tmp / "paket")
        try:
            code, r = self.post("/api/account-import", {"pick": "folder"})
            self.assertTrue(r["ok"], r)
        finally:
            os.environ.pop("REELS_GOZLEMCI_TEST_PICK")


if __name__ == "__main__":
    unittest.main()
