"""Motor: Instagram'ı kalıcı profilli bir tarayıcıda açar, Reels'i izler, kaydırır ve kaydeder.

Instagram'a kendimiz istek göndermeyiz. Yalnızca (1) sayfanın kendi aldığı JSON yanıtlarını dinleriz,
(2) adres çubuğundaki Reel kodunu ve ekrandaki videonun süresini okuruz, (3) istenirse klavye/fare ile kaydırırız.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional
from urllib.parse import urlsplit

from . import __version__, extract, winbrowsers
from .paths import app_data_dir, screen_size
from .report import redact
from .store import Store, iso

URL_CODE = re.compile(r"/(?:reels?)/([A-Za-z0-9_-]{5,})")

VIDEO_JS = r"""() => {
  const vh = innerHeight, vw = innerWidth;
  let best = null, bestArea = 0;
  for (const v of document.querySelectorAll('video')) {
    const r = v.getBoundingClientRect();
    const w = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
    const h = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
    if (w * h > bestArea) { bestArea = w * h; best = v; }
  }
  if (!best || bestArea < 20000) return {ok: false};
  let code = null, el = best;
  for (let i = 0; i < 12 && el; i++, el = el.parentElement) {
    if (el.querySelectorAll && el.querySelectorAll('video').length > 1) break;
    const a = el.querySelector && el.querySelector('a[href*="/reel/"], a[href*="/reels/"]');
    if (a) {
      const m = (a.getAttribute('href') || '').match(/\/reels?\/([A-Za-z0-9_-]{5,})/);
      if (m) { code = m[1]; break; }
    }
  }
  const d = best.duration;
  return {ok: true, t: best.currentTime, d: (isFinite(d) && d > 0) ? d : null,
          paused: best.paused, ended: best.ended, code: code};
}"""


class BrowserGone(Exception):
    pass


@dataclass
class View:
    code: str
    t_wall: float
    t_mono: float
    seq: int
    target: tuple = ("t", 4.0)
    watched: float = 0.0
    loops: int = 0
    last_t: Optional[float] = None
    duration: Optional[float] = None
    starred: bool = False
    notes: List[str] = field(default_factory=list)
    no_scroll_until: float = 0.0

    def dwell(self) -> float:
        return time.monotonic() - self.t_mono


class Engine:
    def __init__(self, store: Store, data_dir=None):
        self.store = store
        self.data_dir = data_dir or app_data_dir()
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._cmds: "Deque[tuple]" = deque()
        self._logs: Deque[Dict[str, str]] = deque(maxlen=400)
        self.settings: Dict[str, Any] = {}
        # durum
        self.phase = "hazir"
        self.message = "Başlat'a bas; açılan tarayıcıda Instagram'a giriş yap."
        self.logged_in = False
        self.paused = False
        self.browser_name = ""
        self.session_id = 0
        self.views = 0
        self.responses_seen = 0
        self.responses_with_media = 0
        self.scroll_fail = 0
        self.current: Dict[str, Any] = {}
        self.current_code = ""
        self.page_url = ""
        self.next_in: Optional[float] = None
        self._view: Optional[View] = None
        self._auto_leave = False
        self._seq = 0
        self._stop = threading.Event()
        self._finished_at = 0.0
        self._limit_reached = False
        self._last_logged_url = ""
        self._login_page_shown = False
        self._cookie_names_logged = False
        self._quiet_responses = 0

    # ------------------------------------------------------------------ dışarıya
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def log(self, level: str, text: str) -> None:
        entry = {"t": time.strftime("%H:%M:%S"), "level": level, "text": redact(str(text))}
        with self._lock:
            self._logs.append(entry)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            cur = dict(self.current)
            v = self._view
            if v is not None:
                cur["dwell"] = round(v.dwell(), 1)
                cur["watched"] = round(v.watched, 1)
                cur["duration"] = round(v.duration, 1) if v.duration else cur.get("duration")
                cur["starred"] = v.starred
            return {
                "running": self.running, "phase": self.phase, "message": self.message, "logged_in": self.logged_in,
                "paused": self.paused, "auto": bool(self.settings.get("auto")), "scenario": self.settings.get("scenario"),
                "browser": self.browser_name, "views": self.views, "reels_known": self.store.count_reels(),
                "responses_seen": self.responses_seen, "responses_with_media": self.responses_with_media,
                "scroll_fail": self.scroll_fail, "current": cur, "current_code": self.current_code,
                "next_in": self.next_in, "url": self.page_url,
                "recent": self.store.recent_views(self.session_id, 8) if self.session_id else [],
                "logs": list(self._logs)[-120:],
            }

    def command(self, name: str, **kw: Any) -> None:
        with self._lock:
            self._cmds.append((name, kw))

    def start(self, settings: Dict[str, Any]) -> bool:
        with self._lock:
            if self.running:
                return False
            self.settings = dict(settings)
            self._stop.clear()
            self._cmds.clear()
            self.paused = False
            self.views = 0
            self._limit_reached = False
            self._login_page_shown = False
            self._cookie_names_logged = False
            self._quiet_responses = 0
            self._last_logged_url = ""
            self._seq = 0
            self.scroll_fail = 0
            self.phase, self.message = "tarayici", "Tarayıcı açılıyor…"
            self._thread = threading.Thread(target=self._thread_main, name="engine", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop.set()

    def wait(self, timeout: float = 20) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    def update_settings(self, settings: Dict[str, Any]) -> None:
        """Çalışırken senaryo/aralık değişince bir sonraki Reel'den itibaren geçerli olur."""
        with self._lock:
            self.settings.update(settings)

    # ------------------------------------------------------------------ iş parçacığı
    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:  # beklenmeyen: rapora düşsün
            import traceback
            self.log("error", "Beklenmeyen hata: " + "".join(traceback.format_exception_only(type(exc), exc)).strip())
            self.log("debug", traceback.format_exc()[-1500:])
            self.phase, self.message = "hata", f"Beklenmeyen hata: {exc}"
        finally:
            self._finished_at = time.time()

    async def _main(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self.context = None
        self.page = None
        closed_by_user = False
        try:
            await self._launch()
            self.session_id = self.store.start_session(
                self.settings.get("scenario", ""), {k: v for k, v in self.settings.items() if k != "out_dir"},
                __version__, self.browser_name)
            self.log("info", f"Oturum {self.session_id} başladı ({self.browser_name}).")
            await self._loop()
        except BrowserGone:
            self.log("warn", f"Tarayıcı penceresi kapatıldı (son adres: {urlsplit(self.page_url).path[:60] or '-'}, "
                             f"giriş: {'var' if self.logged_in else 'yok'}).")
            closed_by_user = True
        finally:
            await self._close_view("elle")
            if self.session_id:
                self.store.end_session(self.session_id)
            await self._shutdown()
            if self.phase != "hata":
                self.phase = "bitti"
                self.message = (("Tarayıcı penceresi kapandı. " if closed_by_user else "")
                                + f"{self.views} izleme kaydedildi.")

    # ------------------------------------------------------------------ tarayıcı
    def _channels(self) -> List[str]:
        wanted = os.environ.get("REELS_GOZLEMCI_BROWSER", "")
        if wanted:
            return [wanted]
        order: List[str] = []
        if winbrowsers.healthy_edge():
            order.append("msedge")
        elif winbrowsers.find_edge():
            self.log("warn", "Edge kurulumu bozuk görünüyor, başka tarayıcıya geçiliyor.")
        if winbrowsers.healthy_chrome():
            order.append("chrome")
        order.append("chromium")
        return order

    def _window_args(self) -> List[str]:
        sw, sh = screen_size()
        w, h = max(700, sw - 430), max(600, sh - 60)
        args = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check",
                f"--window-size={w},{h}", "--window-position=0,0"]
        if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
            args.append("--no-sandbox")
        return args

    async def _launch(self) -> None:
        headless = os.environ.get("REELS_GOZLEMCI_HEADLESS") == "1"
        errors: List[str] = []
        installed_tried = False
        channels = self._channels()
        i = 0
        while i < len(channels):
            channel = channels[i]
            i += 1
            profile = self.data_dir / f"tarayici-{channel}"
            profile.mkdir(parents=True, exist_ok=True)
            kwargs: Dict[str, Any] = dict(
                headless=headless, args=self._window_args(), ignore_default_args=["--enable-automation"],
                no_viewport=True, locale="tr-TR", accept_downloads=False, timeout=60000)
            if channel != "chromium":
                kwargs["channel"] = channel
            else:
                exe = winbrowsers.bundled_chromium_exe()
                if exe:
                    kwargs["executable_path"] = exe
            try:
                self.context = await self._pw.chromium.launch_persistent_context(str(profile), **kwargs)
            except Exception as exc:
                text = str(exc)
                errors.append(f"{channel}: {text.splitlines()[0] if text else exc}")
                if "ProcessSingleton" in text or "already in use" in text:
                    raise RuntimeError("Tarayıcı profili başka bir pencerede açık. Öbür Reels Gözlemci penceresini kapat.")
                if channel == "chromium" and not installed_tried and not os.environ.get("REELS_GOZLEMCI_BROWSER"):
                    installed_tried = True
                    self.phase, self.message = "tarayici", "Tarayıcı bileşeni indiriliyor…"
                    if winbrowsers.install_bundled_chromium(self.log):
                        channels.append("chromium")
                continue
            self.browser_name = {"msedge": "Microsoft Edge", "chrome": "Google Chrome"}.get(channel, "Chromium")
            break
        else:
            raise RuntimeError("Hiçbir tarayıcı açılamadı: " + " | ".join(errors))

        self.context.on("response", self._on_response)
        pages = self.context.pages
        self.page = pages[0] if pages else await self.context.new_page()
        self.page.on("close", lambda *_: None)
        await self._goto(self.settings["start_url"])

    async def _goto(self, url: str) -> None:
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            if self.page.is_closed():
                raise BrowserGone()
            self.log("warn", f"Sayfa açılamadı ({url}): {str(exc).splitlines()[0]}")

    async def _shutdown(self) -> None:
        if self.context is not None:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None
        try:
            await self._pw.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------ sayfanın kendi yanıtları
    async def _on_response(self, response: Any) -> None:
        try:
            req = response.request
            if req.resource_type not in ("fetch", "xhr"):
                return
            url = response.url
            if "graphql" not in url and "/api/v1/" not in url:
                return
            if response.status != 200:
                return
            text = await response.text()
        except Exception:
            return
        self.responses_seen += 1
        try:
            records = extract.extract_media(text)
        except Exception as exc:
            self.log("debug", f"yanıt çözülemedi: {exc}")
            return
        if not records:
            self._quiet_responses += 1
            if self._quiet_responses <= 25:
                self.log("debug", f"yanıt (Reel yok): {urlsplit(url).path[-50:]} {len(text)} bayt")
            return
        self.responses_with_media += 1
        new = 0
        for rec in records:
            if self.store.upsert_media(rec):
                new += 1
            if rec["code"] == self.current_code:
                self._refresh_current(rec["code"])
        self.log("debug", f"yanıt: {len(records)} kayıt ({new} yeni) {urlsplit(url).path[-40:]}")

    def _refresh_current(self, code: str) -> None:
        info = self.store.get_reel(code) or {}
        with self._lock:
            self.current = {
                "code": code, "username": info.get("username", ""), "caption": (info.get("caption") or "")[:300],
                "likes": info.get("like_count"), "comments": info.get("comment_count"),
                "plays": info.get("play_count"), "shares": info.get("reshare_count"), "music": info.get("music", ""),
                "is_ad": bool(info.get("is_ad")), "duration": info.get("duration"), "known": bool(info),
            }

    # ------------------------------------------------------------------ ana döngü
    async def _is_logged_in(self) -> bool:
        try:
            cookies = await self.context.cookies(self.settings["start_url"])
        except Exception:
            return False
        return any(c.get("name") == "sessionid" and c.get("value") for c in cookies)

    async def _log_cookie_names(self) -> None:
        """Teşhis için: hangi çerezlerin var olduğu (yalnız adlar; değerler yazılmaz)."""
        try:
            cookies = await self.context.cookies()
            names = sorted({c.get("name", "") for c in cookies})
            self.log("info", "Çerez adları: " + (", ".join(names) if names else "(yok)"))
        except Exception as exc:
            self.log("debug", f"çerezler okunamadı: {exc}")

    def _page_alive(self) -> bool:
        """Ana sekme kapandıysa ama başka sekme açıksa ona geç; hiç sekme kalmadıysa tarayıcı gitmiştir."""
        if self.page is None:
            return False
        if not self.page.is_closed():
            return True
        try:
            others = [p for p in (self.context.pages if self.context else []) if not p.is_closed()]
        except Exception:
            others = []
        if others:
            self.page = others[-1]
            self.log("info", "Sekme değişti: " + urlsplit(self.page.url).path[:60])
            return True
        return False

    async def _loop(self) -> None:
        tick = 0.25
        last_login_check = 0.0
        went_to_start_after_login = False
        while not self._stop.is_set():
            if not self._page_alive():
                raise BrowserGone()
            await self._handle_commands()
            now = time.monotonic()
            self.page_url = self.page.url
            if self.page_url != self._last_logged_url:
                self._last_logged_url = self.page_url
                self.log("info", "Sayfa: " + urlsplit(self.page_url).path[:80])

            if now - last_login_check > 2.0 or not self.logged_in:
                if now - last_login_check > 1.0:
                    last_login_check = now
                    was = self.logged_in
                    self.logged_in = await self._is_logged_in()
                    if self.logged_in and not was:
                        self.log("ok", "Giriş yapıldı.")
                        if not went_to_start_after_login and "/reel" not in self.page_url:
                            went_to_start_after_login = True
                            await self._goto(self.settings["start_url"])
                    if was and not self.logged_in:
                        self.log("warn", "Oturum kapandı.")
            if not self.logged_in:
                if not self._login_page_shown and "/accounts/login" not in self.page_url and "/challenge" not in self.page_url:
                    self._login_page_shown = True       # giriş yokken Reels'te kalma: doğrudan giriş sayfası
                    if not self._cookie_names_logged:
                        self._cookie_names_logged = True
                        await self._log_cookie_names()
                    self.log("info", "Giriş yok: giriş sayfası açılıyor.")
                    await self._goto(self.settings["start_url"].split("/reels")[0].rstrip("/") + "/accounts/login/"
                                     if "/reels" in self.settings["start_url"] else self.settings["start_url"])
                if "/challenge" in self.page_url:
                    self.phase, self.message = "giris", "Instagram doğrulama istiyor. Tarayıcı penceresinde tamamla."
                else:
                    self.phase, self.message = "giris", "Açılan tarayıcı penceresinde Instagram'a giriş yap."
                await asyncio.sleep(0.5)
                continue

            await self._track()
            await asyncio.sleep(tick)

            if self._limit_reached:
                self.log("info", f"{self.settings.get('limit')} izleme tamamlandı, duruyor.")
                break

    async def _handle_commands(self) -> None:
        while True:
            with self._lock:
                if not self._cmds:
                    return
                name, kw = self._cmds.popleft()
            if name == "pause":
                self.paused = True
                self.log("info", "Duraklatıldı (otomatik kaydırma kapalı, kayıt sürüyor).")
            elif name == "resume":
                self.paused = False
                if self._view is not None:
                    self._view.target = self._pick_target()
                self.log("info", "Devam ediyor.")
            elif name == "star" and self._view is not None:
                self._view.starred = not self._view.starred
                self.log("info", ("İşaretlendi: " if self._view.starred else "İşaret kalktı: ") + self._view.code)
            elif name == "note" and self._view is not None:
                text = str(kw.get("text") or "").strip()
                if text:
                    self._view.notes.append(text[:500])
                    self.log("info", f"Not eklendi ({self._view.code}).")

    # ------------------------------------------------------------------ izleme
    def _pick_target(self) -> tuple:
        s = self.settings
        sc = s.get("scenario", "fixed")
        cap = float(s.get("full_cap_s", 60))
        if sc == "skip":
            return ("t", random.uniform(1.0, 2.0))
        if sc == "full":
            return ("full", cap)
        if sc == "mix":
            r = random.random()
            if r < 0.6:
                return ("t", random.uniform(1.5, 4.0))
            if r < 0.9:
                return ("t", random.uniform(5.0, 15.0))
            return ("full", cap)
        lo, hi = float(s.get("min_s", 3)), float(s.get("max_s", 6))
        return ("t", random.uniform(lo, hi))

    async def _read_video(self) -> Dict[str, Any]:
        try:
            return await self.page.evaluate(VIDEO_JS) or {"ok": False}
        except Exception:
            if not self._page_alive():
                raise BrowserGone()
            return {"ok": False}

    async def _track(self) -> None:
        url = self.page_url
        m = URL_CODE.search(urlsplit(url).path)
        info = await self._read_video()
        code = m.group(1) if m else None
        if code is None and "/reels" in urlsplit(url).path and info.get("ok"):
            code = info.get("code")
        v = self._view

        if code != (v.code if v else None):
            await self._close_view("otomatik" if self._auto_leave else "elle")
            self._auto_leave = False
            v = None
            limit = int(self.settings.get("limit") or 0)
            if limit and self.views >= limit:
                self._limit_reached = True        # sınıra ulaşıldı: yeni Reel'i kaydetmeden dur
                return
            if code:
                self._seq += 1
                v = View(code=code, t_wall=time.time(), t_mono=time.monotonic(), seq=self._seq,
                         target=self._pick_target())
                self._view = v
                self.current_code = code
                self._refresh_current(code)
                if not self.current.get("known"):
                    self.log("debug", f"Reel {code}: ayrıntısı henüz gelmedi (yanıt bekleniyor).")
            else:
                self.current_code = ""
                with self._lock:
                    self.current = {}

        if v is None:
            self.phase = "calisiyor"
            self.message = "Reels sayfasını aç (instagram.com/reels)."
            self.next_in = None
            return

        if info.get("ok"):
            t, d = float(info.get("t") or 0.0), info.get("d")
            if d:
                v.duration = float(d)
            if v.last_t is not None:
                delta = t - v.last_t
                if 0 <= delta < 3 and not info.get("paused"):
                    v.watched += delta
                elif delta < 0 and v.duration and v.last_t > v.duration * 0.5 and not info.get("paused"):
                    v.loops += 1                              # video başa sardı
                    v.watched += max(0.0, v.duration - v.last_t) + t
            v.last_t = t
        if not self.current.get("known"):
            self._refresh_current(v.code)

        self.phase = "duraklatildi" if self.paused else "calisiyor"
        auto = bool(self.settings.get("auto")) and not self.paused
        dwell = v.dwell()
        kind, val = v.target
        if kind == "full" and not (v.duration or info.get("d")):
            kind, val = "t", min(val, 8.0)                    # süresi okunamayan (görsel/reklam): kısa bekle
        if kind == "t":
            self.next_in = max(0.0, val - dwell) if auto else None
            ready = dwell >= val
        else:
            done = v.loops >= 1 or (v.duration is not None and v.watched >= v.duration - 0.3)
            self.next_in = max(0.0, val - dwell) if auto else None
            ready = done or dwell >= val
        self.message = ("Duraklatıldı — elle gezebilirsin." if self.paused else
                        ("Otomatik kaydırıyor." if auto else "İzliyor (otomatik kaydırma kapalı)."))
        if auto and ready and time.monotonic() >= v.no_scroll_until:
            await self._scroll()

    async def _close_view(self, action: str) -> None:
        v = self._view
        if v is None:
            return
        self._view = None
        self.store.add_view({
            "session_id": self.session_id, "seq": v.seq, "code": v.code, "t_start": v.t_wall, "t_end": time.time(),
            "dwell": round(v.dwell(), 2), "watched": round(v.watched, 2), "duration": v.duration, "loops": v.loops,
            "action": action, "starred": 1 if v.starred else 0, "note": " | ".join(v.notes)})
        self.views += 1

    async def _scroll(self) -> None:
        before = self._view.code if self._view else None
        self._auto_leave = True
        for method in ("key", "wheel"):
            try:
                if method == "key":
                    await self.page.keyboard.press("ArrowDown")
                else:
                    size = await self.page.evaluate("() => [innerWidth, innerHeight]")
                    await self.page.mouse.move(size[0] / 2, size[1] / 2)
                    await self.page.mouse.wheel(0, size[1] * 0.9)
            except Exception as exc:
                if not self._page_alive():
                    raise BrowserGone()
                self.log("warn", f"Kaydırma komutu hata verdi ({method}): {str(exc).splitlines()[0]}")
                continue
            for _ in range(14):                                # en çok ~2.8 sn bekle
                await asyncio.sleep(0.2)
                if not self._page_alive():
                    raise BrowserGone()
                m = URL_CODE.search(urlsplit(self.page.url).path)
                info = await self._read_video()
                new = m.group(1) if m else (info.get("code") if info.get("ok") else None)
                if new and new != before:
                    self.scroll_fail = 0
                    if method == "wheel":
                        self.log("debug", "Klavye işe yaramadı, fare tekerleği ile kaydırıldı.")
                    return
            self.log("debug", f"Kaydırma ({method}) sonuç vermedi.")
        self._auto_leave = False
        self.scroll_fail += 1
        if self.scroll_fail >= 3:
            self.paused = True
            self.log("warn", "Kaydırma 3 kez üst üste olmadı; duraklatıldı. Sayfada Reels açık mı? Elle kaydırıp Devam'a bas.")
            self.scroll_fail = 0
        else:
            if self._view is not None:
                self._view.no_scroll_until = time.monotonic() + 2.0   # biraz sonra yeniden dene
