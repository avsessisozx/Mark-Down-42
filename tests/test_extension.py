"""Chrome eklentisi (Giriş Köprüsü) uçtan uca: gerçek Chromium'a eklenti yüklenir, program sunucusuna oturum gönderir.

Çalıştırmak için Playwright'ın kendi Chromium'u gerekir (Edge/Chrome kanalı eklenti yüklemeyi desteklemeyebilir):
  REELS_GOZLEMCI_BROWSER=chromium python -m unittest tests.test_extension
Başka bir tarayıcı seçiliyse testler atlanır.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

os.environ.setdefault("REELS_GOZLEMCI_BROWSER", "chromium")
os.environ["REELS_GOZLEMCI_NO_OPEN"] = "1"

from reels_gozlemci import server
from tests import fake_instagram as fake

SID = "12345678%3Aabcdefghij%3A5%3AAYdxyz"


def make_test_extension(dst: Path) -> Path:
    """Gerçek eklentinin kopyası; yalnızca adresler sahte Instagram'ın (127.0.0.1) adresine çevrilir."""
    shutil.copytree(server.extension_source(), dst)
    manifest = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    manifest["host_permissions"] = ["http://127.0.0.1/*"]
    manifest["content_scripts"][0]["matches"] = ["http://127.0.0.1/*"]
    (dst / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    bg = (dst / "background.js").read_text(encoding="utf-8")
    assert 'const COOKIE_DOMAIN = "instagram.com";' in bg
    (dst / "background.js").write_text(bg.replace('const COOKIE_DOMAIN = "instagram.com";',
                                                   'const COOKIE_DOMAIN = "127.0.0.1";'), encoding="utf-8")
    return dst


@unittest.skipUnless(os.environ.get("REELS_GOZLEMCI_BROWSER", "chromium") == "chromium",
                     "eklenti testi yalnızca Playwright Chromium ile çalışır")
class ExtensionE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fsrv, cls.fake, cls.base = fake.start()
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.state = server.AppState(Path(cls.tmp.name) / "data")
        cls.http = server.start_server(cls.state)
        cls.ext = make_test_extension(Path(cls.tmp.name) / "ext")

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.state.store.close()
        cls.fsrv.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        self.state.pending_cookies = {}
        self.state.ext_token = ""

    def start_chrome_login(self) -> str:
        req = urllib.request.Request(f"http://127.0.0.1:{self.state.port}/api/login-chrome", data=b"{}",
                                     headers={"Content-Type": "application/json", "X-Token": self.state.token}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertTrue(json.loads(r.read())["ok"])
        self.assertTrue(self.state.chrome_waiting())
        return self.state.ext_token

    def open_chrome(self, profile: str):
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        args = [f"--disable-extensions-except={self.ext}", f"--load-extension={self.ext}"]
        if os.geteuid() == 0:
            args.append("--no-sandbox")
        ctx = pw.chromium.launch_persistent_context(str(Path(self.tmp.name) / profile), channel="chromium", headless=True, args=args)
        self.addCleanup(pw.stop)
        self.addCleanup(ctx.close)
        return ctx

    def wait_pending(self, timeout=25):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.state.pending_cookies:
                return self.state.pending_cookies
            time.sleep(0.3)
        self.fail("eklentiden oturum gelmedi")

    def banner(self, page, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            text = page.evaluate("document.getElementById('ii-kopru-bilgi') && document.getElementById('ii-kopru-bilgi').textContent")
            if text:
                return text
            time.sleep(0.3)
        return ""

    def test_1_logged_in_chrome_sends_session(self):
        token = self.start_chrome_login()
        ctx = self.open_chrome("p1")
        ctx.add_cookies([{"name": "sessionid", "value": SID, "url": self.base + "/", "httpOnly": True},
                         {"name": "mid", "value": "MIDDEGERI", "url": self.base + "/"}])
        page = ctx.new_page()
        page.goto(f"{self.base}/accounts/login/#ii={self.state.port}.{token}")
        cookies = self.wait_pending()
        self.assertEqual(cookies["sessionid"], SID)
        self.assertEqual(cookies["mid"], "MIDDEGERI")
        self.assertIn("gönderildi", self.banner(page))
        self.assertNotIn("#ii=", page.url, "kod adres çubuğundan silinmeli")
        self.assertFalse(self.state.chrome_waiting(), "kod tek kullanımlık olmalı")
        logs = " ".join(e["text"] for e in self.state.engine.snapshot()["logs"])
        self.assertNotIn(SID, logs)

    def test_2_login_after_opening_sends_session_automatically(self):
        token = self.start_chrome_login()
        ctx = self.open_chrome("p2")
        page = ctx.new_page()
        page.goto(f"{self.base}/accounts/login/#ii={self.state.port}.{token}")
        self.assertIn("giriş yap", self.banner(page))
        self.assertEqual(self.state.pending_cookies, {})
        ctx.add_cookies([{"name": "sessionid", "value": SID, "url": self.base + "/", "httpOnly": True}])
        self.assertEqual(self.wait_pending()["sessionid"], SID)

    def test_3_wrong_code_is_rejected(self):
        self.start_chrome_login()
        ctx = self.open_chrome("p3")
        ctx.add_cookies([{"name": "sessionid", "value": SID, "url": self.base + "/", "httpOnly": True}])
        page = ctx.new_page()
        page.goto(f"{self.base}/accounts/login/#ii={self.state.port}.YanlisKod12345")
        self.assertIn("kabul etmedi", self.banner(page))
        self.assertEqual(self.state.pending_cookies, {})


if __name__ == "__main__":
    unittest.main()
