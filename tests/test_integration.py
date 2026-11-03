"""Uçtan uca testler: gerçek Chromium/Edge + sahte Instagram Reels sitesi."""

import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("REELS_GOZLEMCI_BROWSER", "chromium")
os.environ.setdefault("REELS_GOZLEMCI_HEADLESS", "1")
os.environ["REELS_GOZLEMCI_NO_OPEN"] = "1"

from reels_gozlemci import paths
from reels_gozlemci.engine import Engine
from reels_gozlemci.server import AppState, start_server
from reels_gozlemci.store import Store
from tests import fake_instagram as fake


def wait_for(cond, timeout=30.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


class EngineBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.fake, cls.base = fake.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.fake.logged_in = True
        self.tmp = Path(tempfile.mkdtemp(prefix="rg-test-"))
        self.store = Store(self.tmp / "t.sqlite3")
        self.engine = Engine(self.store, self.tmp)

    def tearDown(self):
        self.engine.stop()
        self.engine.wait(25)
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, **kw):
        s = {"scenario": "fixed", "min_s": 0.6, "max_s": 0.9, "auto": True, "limit": 0,
             "start_url": self.base + "/reels/", "full_cap_s": 20}
        s.update(kw)
        return paths.clean_settings(s)

    def run_until_done(self, settings, timeout=60):
        self.assertTrue(self.engine.start(settings))
        self.assertTrue(wait_for(lambda: not self.engine.running, timeout), "motor bitmedi: " + str(self.engine.snapshot()["logs"][-8:]))


class AutoScrollTests(EngineBase):
    def test_a_fixed_interval_records_views_and_metadata(self):
        self.run_until_done(self.settings(limit=6))
        views = self.store.export_views()
        self.assertEqual(len(views), 6, views)
        codes = [v["code"] for v in views]
        self.assertEqual(codes, [fake.code_of(i) for i in range(6)])
        for v in views[:5]:
            self.assertEqual(v["action"], "otomatik")
            self.assertGreater(v["dwell"], 0.5)
            self.assertLess(v["dwell"], 3.5)
            self.assertGreater(v["watched"], 0.3)
            self.assertEqual(v["duration"], fake.DURATION)
        # sayfanın kendi yanıtlarından gelen bilgiler
        self.assertEqual(views[0]["username"], "kisi0")
        self.assertEqual(views[1]["like_count"], 107)
        self.assertEqual(views[2]["music"], "Şarkı — Sanatçı")
        self.assertEqual(views[3]["is_ad"], 1)       # sahte sitede 3. Reel reklam
        self.assertEqual(views[4]["is_ad"], 0)
        self.assertGreaterEqual(self.engine.responses_with_media, 1)

    def test_b_pause_stops_scrolling_and_resume_continues(self):
        self.engine.start(self.settings())
        self.assertTrue(wait_for(lambda: self.engine.views >= 2, 30))
        self.engine.command("pause")
        self.assertTrue(wait_for(lambda: self.engine.paused, 5))
        time.sleep(0.8)
        code_at_pause, views_at_pause = self.engine.current_code, self.engine.views
        time.sleep(3.0)
        self.assertEqual(self.engine.current_code, code_at_pause, "duraklatılmışken kaydırdı")
        self.assertEqual(self.engine.views, views_at_pause)
        self.assertEqual(self.engine.snapshot()["phase"], "duraklatildi")
        self.engine.command("resume")
        self.assertTrue(wait_for(lambda: self.engine.current_code != code_at_pause, 15), "devam edince kaydırmadı")

    def test_c_star_and_note_are_saved(self):
        self.engine.start(self.settings(min_s=1.5, max_s=1.6))
        self.assertTrue(wait_for(lambda: self.engine.current_code != "", 30))
        self.engine.command("star")
        self.engine.command("note", text="ilginç müzik")
        self.assertTrue(wait_for(lambda: self.engine.views >= 1, 15))
        self.engine.stop()
        self.engine.wait(25)
        first = self.store.export_views()[0]
        self.assertEqual(first["starred"], 1)
        self.assertEqual(first["note"], "ilginç müzik")

    def test_d_keyboard_blocked_falls_back_to_wheel(self):
        self.run_until_done(self.settings(limit=3, start_url=self.base + "/reels/?nokey=1"))
        views = self.store.export_views()
        self.assertEqual([v["code"] for v in views], [fake.code_of(i) for i in range(3)])
        self.assertEqual(views[0]["action"], "otomatik")

    def test_e_no_url_change_reads_code_from_page(self):
        self.run_until_done(self.settings(limit=3, start_url=self.base + "/reels/?nourl=1"))
        views = self.store.export_views()
        self.assertEqual([v["code"] for v in views], [fake.code_of(i) for i in range(3)])

    def test_f_full_scenario_waits_for_video_end(self):
        self.run_until_done(self.settings(scenario="full", limit=2), timeout=60)
        views = self.store.export_views()
        self.assertEqual(len(views), 2)
        first = views[0]
        self.assertGreater(first["dwell"], fake.DURATION - 0.8)
        self.assertLess(first["dwell"], fake.DURATION + 3.0)
        self.assertGreater(first["watched"], fake.DURATION - 1.0)

    def test_g_skip_scenario_is_quick(self):
        self.run_until_done(self.settings(scenario="skip", limit=4))
        views = self.store.export_views()
        self.assertEqual(len(views), 4)
        for v in views[:3]:
            self.assertLess(v["dwell"], 3.5)

    def test_h_auto_off_only_observes(self):
        self.engine.start(self.settings(auto=False))
        self.assertTrue(wait_for(lambda: self.engine.current_code != "", 30))
        first = self.engine.current_code
        time.sleep(3.0)
        self.assertEqual(self.engine.current_code, first)
        self.assertEqual(self.engine.views, 0)


class LoginTests(EngineBase):
    def test_waits_for_login_then_works(self):
        self.fake.logged_in = False
        self.engine.start(self.settings(limit=2))
        self.assertTrue(wait_for(lambda: self.engine.phase == "giris", 30), self.engine.phase)
        time.sleep(1.0)
        self.assertEqual(self.engine.views, 0)
        self.fake.logged_in = True
        self.assertTrue(wait_for(lambda: not self.engine.running, 60), str(self.engine.snapshot()["logs"][-6:]))
        self.assertEqual(len(self.store.export_views()), 2)
        self.assertTrue(self.engine.logged_in)


class ServerTests(EngineBase):
    def setUp(self):
        super().setUp()
        self._old_data = os.environ.get("REELS_GOZLEMCI_DATA")
        os.environ["REELS_GOZLEMCI_DATA"] = str(self.tmp / "app")
        self.state = AppState(self.tmp / "app")
        self.http = start_server(self.state)
        self.url = f"http://127.0.0.1:{self.state.port}"

    def tearDown(self):
        self.state.engine.stop()
        self.state.engine.wait(25)
        self.http.shutdown()
        self.state.store.close()
        if self._old_data is None:
            os.environ.pop("REELS_GOZLEMCI_DATA", None)
        else:
            os.environ["REELS_GOZLEMCI_DATA"] = self._old_data
        super().tearDown()

    def call(self, path, body=None, token=True):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method="POST" if body is not None else "GET")
        if token:
            req.add_header("X-Token", self.state.token)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_api_flow(self):
        with urllib.request.urlopen(self.url + "/", timeout=10) as r:
            html = r.read().decode("utf-8")
        self.assertIn(self.state.token, html)
        status, snap = self.call("/api/state")
        self.assertEqual((status, snap["phase"]), (200, "hazir"))
        self.assertEqual(self.call("/api/start", {}, token=False)[0], 403)
        status, res = self.call("/api/settings", {"scenario": "skip", "limit": 2, "start_url": self.base + "/reels/"})
        self.assertTrue(res["ok"])
        self.assertEqual(self.call("/api/start", {})[1]["ok"], True)
        self.assertTrue(wait_for(lambda: not self.state.engine.running, 60))
        status, snap = self.call("/api/state")
        self.assertEqual(snap["views"], 2)
        self.assertEqual(len(snap["recent"]), 2)
        status, res = self.call("/api/export", {})
        self.assertTrue(res["ok"])
        self.assertTrue((Path(res["folder"]) / "gorunumler.csv").is_file())
        status, rep = self.call("/api/report")
        self.assertIn("Reels Gözlemci raporu", rep["text"])
        self.assertNotIn("abc123", rep["text"])


if __name__ == "__main__":
    unittest.main()
