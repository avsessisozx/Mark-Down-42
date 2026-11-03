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


class AccountTests(EngineBase):
    def logs(self):
        return " | ".join(e["text"] for e in self.engine.snapshot()["logs"])

    def test_cookies_given_at_start_skip_login(self):
        self.fake.logged_in = False
        self.assertTrue(self.engine.start(self.settings(limit=3), {"sessionid": "abc123", "mid": "MIDX"}))
        self.assertTrue(wait_for(lambda: not self.engine.running, 60), self.logs())
        self.assertEqual(len(self.store.export_views()), 3)
        self.assertIn("Hesap çerezleri yerleştirildi", self.logs())
        self.assertNotIn("abc123", self.logs())          # değerler günlüğe yazılmaz

    def test_account_verified_message(self):
        self.fake.logged_in = False
        self.engine.start(self.settings(), {"sessionid": "abc123"})
        self.assertTrue(wait_for(lambda: "Hesap doğrulandı" in self.logs(), 40), self.logs())

    def test_rejected_cookie_is_reported(self):
        self.fake.logged_in = False
        self.engine.start(self.settings(), {"sessionid": "badbad1"})
        self.assertTrue(wait_for(lambda: "kabul etmedi" in self.logs(), 40), self.logs())

    def test_account_added_while_running(self):
        self.fake.logged_in = False
        self.engine.start(self.settings())
        self.assertTrue(wait_for(lambda: self.engine.phase == "giris", 30))
        self.engine.command("import_cookies", cookies={"sessionid": "abc123"})
        self.assertTrue(wait_for(lambda: self.engine.views >= 2, 40), self.logs())
        self.assertTrue(self.engine.logged_in)

    def test_logout_clears_session(self):
        self.fake.logged_in = False
        self.engine.start(self.settings(), {"sessionid": "abc123"})
        self.assertTrue(wait_for(lambda: self.engine.views >= 1, 40), self.logs())
        self.engine.command("logout")
        self.assertTrue(wait_for(lambda: not self.engine.logged_in and self.engine.phase == "giris", 20), self.logs())
        self.assertIn("Hesap kaldırıldı", self.logs())


class LoginPageTests(EngineBase):
    def test_logged_out_on_reels_goes_to_login_page(self):
        self.fake.logged_in = False
        with self.fake.lock:
            self.fake.requests.clear()
        self.engine.start(self.settings(start_url=self.base + "/reels/?anon=1"))
        self.assertTrue(wait_for(lambda: self.engine.phase == "giris", 30))
        ok = wait_for(lambda: any(p.startswith("/accounts/login") for _m, p in list(self.fake.requests)), 20)
        self.assertTrue(ok, "giriş sayfasına gitmedi: " + str(self.engine.snapshot()["logs"][-6:]))
        logs = " ".join(e["text"] for e in self.engine.snapshot()["logs"])
        self.assertIn("Çerez adları", logs)


class ClipboardTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "yalnız Windows")
    def test_unicode_survives(self):
        import base64
        import subprocess
        from reels_gozlemci import server
        old = os.environ.pop("REELS_GOZLEMCI_NO_OPEN", None)
        try:
            text = "Reels Gözlemci raporu — çğıöşü İ"
            if not server._copy_to_clipboard(text):
                self.skipTest("Bu ortamda pano kullanılamıyor")
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((Get-Clipboard -Raw)))"],
                capture_output=True, timeout=30).stdout.decode().strip()
            self.assertEqual(base64.b64decode(out).decode("utf-8").strip(), text)
        finally:
            if old is not None:
                os.environ["REELS_GOZLEMCI_NO_OPEN"] = old


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

    def post(self, path, body, headers=None, token=False):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Token", self.state.token)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8")), dict(r.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8")), dict(exc.headers)

    def test_extension_session_flow(self):
        ext = {"Origin": "chrome-extension://abcdefghijklmnop"}
        cookies = {"sessionid": "12345678%3Aabcdefghij%3A5%3AAYdxyz", "mid": "M1", "ig_did": "D-1"}
        # kod istenmeden gelen istek reddedilir
        self.assertEqual(self.post("/api/extension-session", {"token": "x", "cookies": cookies}, ext)[0], 403)
        self.assertEqual(self.post("/api/login-chrome", {}, token=True)[1]["ok"], True)
        self.assertTrue(self.state.chrome_waiting())
        # eklenti kökeni yoksa (sıradan bir web sayfası) reddedilir, kod yanmaz
        self.assertEqual(self.post("/api/extension-session", {"token": self.state.ext_token, "cookies": cookies})[0], 403)
        self.assertEqual(self.post("/api/extension-session", {"token": "yanlis", "cookies": cookies}, ext)[0], 403)
        self.assertTrue(self.state.chrome_waiting())
        # tarayıcı yönlendirmesi (CORS ön denemesi)
        pre = urllib.request.Request(self.url + "/api/extension-session", method="OPTIONS", headers=ext)
        with urllib.request.urlopen(pre, timeout=10) as r:
            self.assertEqual(r.status, 204)
            self.assertEqual(r.headers["Access-Control-Allow-Origin"], ext["Origin"])
        status, body, headers = self.post("/api/extension-session", {"token": self.state.ext_token, "cookies": cookies}, ext)
        self.assertEqual((status, body.get("ok")), (200, True))
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), ext["Origin"])
        self.assertEqual(set(self.state.pending_cookies), {"sessionid", "mid", "ig_did", "ds_user_id"})
        self.assertFalse(self.state.chrome_waiting(), "kod tek kullanımlık olmalı")
        self.assertEqual(self.post("/api/extension-session", {"token": "tekrar", "cookies": cookies}, ext)[0], 403)
        self.assertTrue(self.call("/api/state")[1]["account"]["pending"])

    def test_extension_session_without_login_is_rejected(self):
        ext = {"Origin": "chrome-extension://abcdefghijklmnop"}
        self.post("/api/login-chrome", {}, token=True)
        status, body, _ = self.post("/api/extension-session", {"token": self.state.ext_token, "cookies": {"mid": "M1"}}, ext)
        self.assertEqual(status, 400)
        self.assertEqual(self.state.pending_cookies, {})

    def test_paste_cookie_and_start_uses_account(self):
        self.fake.logged_in = False
        self.assertEqual(self.post("/api/login-cookie", {"cookie": "çöp çöp"}, token=True)[0], 400)
        sid = "12345678%3Aabcdefghij%3A5%3AAYdxyz"
        self.assertEqual(self.post("/api/login-cookie", {"cookie": "sessionid=" + sid}, token=True)[0], 200)
        self.assertEqual(self.state.pending_cookies["sessionid"], sid)
        self.state.pending_cookies = {"sessionid": "abc123"}      # sahte site yalnız bu değeri kabul eder
        self.post("/api/settings", {"scenario": "skip", "limit": 2, "start_url": self.base + "/reels/"}, token=True)
        self.assertTrue(self.post("/api/start", {}, token=True)[1]["ok"])
        self.assertEqual(self.state.pending_cookies, {})
        self.assertTrue(wait_for(lambda: not self.state.engine.running, 60))
        self.assertEqual(self.state.engine.views, 2)

    def test_extension_folder_and_logout(self):
        status, body, _ = self.post("/api/extension-open", {}, token=True)
        self.assertEqual(status, 200)
        self.assertTrue((Path(body["path"]) / "manifest.json").is_file())
        self.assertTrue((Path(body["path"]) / "background.js").is_file())
        (self.state.data_dir / "tarayici-chrome").mkdir()
        (self.state.data_dir / "tarayici-bileseni").mkdir()
        self.assertTrue(self.post("/api/logout", {}, token=True)[1]["ok"])
        self.assertFalse((self.state.data_dir / "tarayici-chrome").exists())
        self.assertTrue((self.state.data_dir / "tarayici-bileseni").exists(), "indirilen tarayıcı bileşenine dokunulmamalı")

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
