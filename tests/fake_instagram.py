"""Testler için sahte Instagram Reels sitesi.

Gerçek Reels sayfasının test edilebilen davranışlarını taklit eder:
  * giriş yoksa /accounts/login/ sayfasına yönlendirir; giriş /__set_login?on=1 ile açılır
  * /reels/ sayfası: her biri tam ekran 30 Reel, kaydırınca adres /reels/<kod>/ olur
  * ArrowDown ya da fare tekerleği ile sonraki Reel'e geçilir
  * Reel bilgisi sayfanın kendi POST /api/graphql isteğiyle 10'ar 10'ar gelir (gerçek biçime benzer iç içe yapı)
  * <video> süresi 4 sn; geçerli videonun currentTime'ı ilerler, 4 sn'de başa sarar
Sorgu parametreleri: nokey=1 (klavye çalışmaz, yalnız tekerlek), nourl=1 (adres değişmez; kod sayfadaki bağlantıdan okunur)
Elle çalıştırmak için: python -m tests.fake_instagram --port 8765
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOTAL = 30
BATCH = 10
DURATION = 4.0
AD_INDEX = 3


def code_of(i: int) -> str:
    return f"Fk{i:05d}x"


def media_of(i: int) -> dict:
    m = {
        "pk": str(5000 + i), "id": f"{5000 + i}_77", "code": code_of(i), "product_type": "clips",
        "taken_at": 1762000000 + i * 60, "media_type": 2,
        "user": {"username": f"kisi{i % 5}", "full_name": f"Kişi {i % 5}", "pk": str(900 + i % 5)},
        "caption": {"text": f"Deneme Reel {i} #deneme #etiket{i % 3}"},
        "like_count": 100 + i * 7, "comment_count": i, "play_count": 1000 + i * 53, "reshare_count": i * 2,
        "video_duration": DURATION, "has_liked": False,
        "clips_metadata": {"music_info": {"music_asset_info": {"title": "Şarkı", "display_artist": "Sanatçı"}}},
        "video_versions": [{"url": "http://x/y.mp4", "width": 720, "height": 1280}],
    }
    if i == AD_INDEX:
        m["ad_id"] = "ad-123"
    return m


def batch_payload(k: int) -> dict:
    edges = [{"node": {"media": media_of(i)}} for i in range(k * BATCH, min(TOTAL, (k + 1) * BATCH))]
    return {"data": {"xdt_api__v1_clips__home__connection_v2": {"edges": edges, "page_info": {"has_next_page": True}}},
            "extensions": {"is_final": True}}


LOGIN_HTML = """<!doctype html><meta charset=utf-8><title>Giriş</title><h1>Instagram girişi (sahte)</h1>
<script>setInterval(async()=>{const r=await fetch('/__poll');const j=await r.json();if(j.ok)location.href='/reels/';},300);</script>"""

REELS_HTML = """<!doctype html><meta charset=utf-8><title>Reels</title>
<style>
 html{scroll-snap-type:y mandatory} body{margin:0;background:#000;color:#fff;font:16px sans-serif}
 section{height:100vh;scroll-snap-align:start;display:flex;align-items:center;justify-content:center;position:relative}
 video{width:360px;height:640px;background:#222}
 a.u{position:absolute;left:20px;bottom:20px;color:#8cf}
</style>
<body>
__SECTIONS__
<script>
const Q = new URLSearchParams(location.search), NOURL = Q.has('nourl'), NOKEY = Q.has('nokey');
const CODES = __CODES__, DUR = __DUR__;
let active = -1, activeSince = performance.now(), loadedBatches = 0, loading = false;
const vids = [...document.querySelectorAll('video')];
vids.forEach((v, i) => {
  Object.defineProperty(v, 'duration', {get: () => DUR});
  Object.defineProperty(v, 'currentTime', {get: () => i === active ? ((performance.now() - activeSince) / 1000) % DUR : 0});
  Object.defineProperty(v, 'paused', {get: () => i !== active});
  Object.defineProperty(v, 'ended', {get: () => false});
});
async function loadBatch() {
  if (loading || loadedBatches * 10 >= CODES.length) return;
  loading = true;
  await fetch('/api/graphql', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: 'batch=' + loadedBatches});
  loadedBatches++; loading = false;
}
const io = new IntersectionObserver(es => {
  for (const e of es) if (e.isIntersecting && e.intersectionRatio > 0.6) {
    const i = +e.target.dataset.i;
    if (i !== active) {
      active = i; activeSince = performance.now();
      if (!NOURL) history.replaceState(null, '', '/reels/' + CODES[i] + '/');
      if (i + 3 >= loadedBatches * 10) loadBatch();
    }
  }
}, {threshold: [0.6]});
document.querySelectorAll('section').forEach(s => io.observe(s));
document.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (NOKEY) return;
    const s = document.querySelectorAll('section')[Math.min(active + 1, CODES.length - 1)];
    if (s) s.scrollIntoView({behavior: 'instant'});
  }
});
loadBatch();
</script>
"""


class FakeState:
    def __init__(self) -> None:
        self.logged_in = False
        self.requests = []
        self.lock = threading.Lock()


def make_handler(state: FakeState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            return

        def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _has_cookie(self):
            return "sessionid=" in (self.headers.get("Cookie") or "")

        def do_GET(self):
            u = urlsplit(self.path)
            with state.lock:
                state.requests.append(("GET", self.path))
            if u.path == "/__poll":
                if state.logged_in:
                    return self._send(200, json.dumps({"ok": True}), "application/json",
                                      {"Set-Cookie": "sessionid=abc123; Path=/"})
                return self._send(200, json.dumps({"ok": False}), "application/json")
            if u.path == "/__set_login":
                state.logged_in = parse_qs(u.query).get("on", ["1"])[0] == "1"
                return self._send(200, "ok", "text/plain")
            if u.path.startswith("/accounts/login"):
                return self._send(200, LOGIN_HTML)
            if u.path.startswith("/reels"):
                if not (self._has_cookie() or state.logged_in):
                    return self._send(302, "", headers={"Location": "/accounts/login/"})
                headers = {"Set-Cookie": "sessionid=abc123; Path=/"} if state.logged_in and not self._has_cookie() else {}
                sections = "\n".join(
                    f'<section data-i="{i}"><video muted playsinline></video>'
                    f'<a class="u" href="/reel/{code_of(i)}/">@kisi{i % 5}</a></section>' for i in range(TOTAL))
                html = (REELS_HTML.replace("__SECTIONS__", sections)
                        .replace("__CODES__", json.dumps([code_of(i) for i in range(TOTAL)]))
                        .replace("__DUR__", str(DURATION)))
                return self._send(200, html, headers=headers)
            self._send(404, "yok", "text/plain")

        def do_POST(self):
            u = urlsplit(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode("utf-8") if n else ""
            with state.lock:
                state.requests.append(("POST", self.path))
            if u.path == "/api/graphql":
                k = int(parse_qs(body).get("batch", ["0"])[0])
                return self._send(200, json.dumps(batch_payload(k)), "application/json")
            self._send(404, "yok", "text/plain")

    return H


def start(port: int = 0):
    state = FakeState()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state, f"http://127.0.0.1:{server.server_address[1]}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    srv, st, base = start(a.port)
    st.logged_in = True
    print("Sahte Instagram:", base + "/reels/")
    while True:
        time.sleep(1)
