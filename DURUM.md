# DURUM — Reels Gözlemci

> Claude'un çalışma notu. Yalnızca `durum` dalında durur; `main`'e, README'ye ve sürüm notlarına girmez.
> Yeni sohbette önce bunu oku, repoyu baştan tarama. Her sürümden sonra güncelle.

Son güncelleme: 2026-11-03 · Son sürüm: **v0.1.1** (2026-11-03)

## Ne yapıyor
Instagram Reels'i izleyip kaydıran, normal kullanıcıya görünen her şeyi (hesap, açıklama, müzik, beğeni, yorum,
izlenme, paylaşım, reklam mı) + kullanıcının davranışını (ekranda kalma, gerçek izleme süresi, otomatik/elle) bir
veri setine yazan Windows programı. Amaç: algoritmanın davranışa nasıl tepki verdiğini gözlemlemek (korelasyon, kesin neden değil).
Kullanıcı: Türkçe, programcı değil; kendi kullanacak. Yedek hesapla kullanılmalı (ban riski).

## Teknik
- Python + Playwright. Yerel sunucu (`server.py`, 127.0.0.1, POST'lar için oturum belirteci) + dar kenar arayüz penceresi
  (Edge/Chrome `--app`, `ui/index.html`) ekranın sağında; Instagram penceresi solda (kalıcı profil `tarayici-<kanal>`).
- Instagram'a ELLE İSTEK YOK (Instagram İndirici'nin dersi: elle API çağrıları 404/429/HTML döndü). `engine._on_response`
  sayfanın kendi fetch/XHR (graphql, /api/v1/) yanıtlarını dinler; `extract.py` yapıdan bağımsız: "code"+beğeni/video/klip
  alanı taşıyan her nesne Reel sayılır. Ham kayıt (büyük görsel/video alanları atılmış) zlib ile `reels.raw`'da.
- Geçerli Reel: adres `/reels/<kod>/` (yoksa ekrandaki videonun yakınındaki bağlantı). Video süresi/currentTime sayfadan
  okunur; `watched` = oynatılan süre (başa sarma sayılır), `dwell` = ekranda kalma.
- Kaydırma: ArrowDown; 2.8 sn'de Reel değişmezse fare tekerleği; 3 kez üst üste olmazsa duraklar.
- Senaryolar: fixed (min–max sn rastgele), skip (1–2 sn), full (video bitene dek, üst sınır), mix. Duraklat = otomatik kaydırma
  kapalı, kayıt sürer (`action=elle`). ⭐ işaret + not her izlemeye bağlı.
- Veri: `reels.sqlite3` (sessions/reels/views), dışa aktarma `gorunumler.csv`, `reeller.csv` (`;`, UTF-8 BOM), `veri.json`.
- Oturum: `sessionid` çerezi varsa girişli; kullanıcı kendi pencerede girer. Sürüm güncellenince veri SIFIRLANMAZ (eski projedeki hata).
- `winbrowsers.py` Instagram İndirici'den (Edge bozuksa Chrome/kendi Chromium). Rapor çerez/şifre siler (`report.redact`).
- CI `build.yml`: Windows (birim + gerçek Edge uçtan uca + EXE derle + EXE öz testi); main'e push → Release (`surum-notu.md` gövde).
- Testler: `tests/test_units.py` (8), `tests/test_integration.py` (10; sahte Instagram `tests/fake_instagram.py` + gerçek tarayıcı).
- Dallar: `gelistirme` (çalışma), `main` (sürüm), `durum` (bu not).

## Yapılanlar
- v0.1.0 (2026-11-03): ilk sürüm, yukarıdakilerin hepsi. DOĞRULANDI: sahte Instagram'a karşı (Linux Chromium + CI'da gerçek Edge)
  otomatik kaydırma, duraklat/devam, yıldız/not, klavye engelliyse tekerlek, adres değişmiyorsa sayfadan kod, "sonuna kadar",
  giriş bekleme, sunucu uçları, dışa aktarma, EXE öz testi.

- v0.1.1 (2026-11-03): kullanıcının ilk gerçek raporu (Chrome, Windows 10): oturum 17 sn sürdü, `logged_in=False`, adres
  `/reels/<kod>/` (Instagram girişsizken Reel görünümü açıyor), 4 yanıt, 0 Reel; pencere kapandı. Rapor panoda Türkçe karakterleri
  bozuk geldi (PowerShell stdin OEM kodlaması). Düzeltmeler: pano UTF-8 dosyadan (`server._copy_to_clipboard`, Windows CI testi),
  girişsizken doğrudan `/accounts/login/` açılır, ana sekme kapanınca başka sekmeye geçilir, günlükte sayfa değişimleri + çerez ADLARI +
  Reel içermeyen yanıtlar + kapanma nedeni. Testler: birim 8, uçtan uca 12 (+LoginPageTests, ClipboardTests Windows).
  Kullanıcı girişi yapmadan pencereyi kapatmış olabilir; nedeni kesin değil (yeni rapora bak).

## Yarım / sıradaki
- DOĞRULANMADI (gerçek Instagram): (1) kendi pencerede giriş doğrulama kilidine takılıyor mu (eski projede sıfır profil takılmıştı;
  burada kalıcı profil); (2) gerçek Reels sayfasında ArrowDown kaydırıyor mu, adres /reels/<kod>/ oluyor mu; (3) Reels sayfasının
  yüklediği JSON'da beğeni/yorum/izlenme/paylaşım alanları var mı, hangileri gizli; (4) reklam işareti (`ad_id` vb.) gerçek adlarla uyuşuyor mu;
  (5) pencere yerleşimi (sol/sağ) ve `sessionid` çerez kontrolü.
  Kullanıcıdan istenecek: ilk denemede "Raporu kopyala" çıktısı (günlükte `yanıt: N kayıt` satırları ve `responses_with_media` sayısı).
- Giriş yedekleri (gerekirse): Instagram İndirici'deki Chrome eklentisi + `sessionid` yapıştırma; doğrulama atlatma YOK.
- Sırada: Android sürümü (kural: iki platformda da olmalı), "sadece X'i beğen" senaryosu (hesapta işlem yapar, riskli),
  İngilizce arayüz, analiz aracı (konu etiketleme, grafik, "bu video neden önerildi" tahmini), Hikayeler/gönderi akışı.
- Ek: görünür beğeni durumu (`has_liked`) yalnız yanıttan; kullanıcının elle beğenisini ayrıca yakalamıyoruz.

## Bilinen sorunlar
- Kayıtlı açık hata yok.

## Kurallar
- Sürüm vermeden önce kendin test et; kullanıcıya bozuk sürüm test ettirme
- Test + CI geçtiyse yayınla, sormadan (her seferinde "yayınlayayım mı" sorma)
- Sürüm notlarına ve commit mesajlarına sohbet / oturum bağlantısı (`Claude-Session:`) ekleme
- Sürüm notu yalnız yeni sürümü anlatsın (eski sürümlerin notları birikmesin)
- Önce `gelistirme` dalında CI'ı geçir, sonra `main`'e al (main'e push sürüm yayınlar)
- Hata raporu uygulama içinde kopyalanabilir olsun; kullanıcıya talimat kısa numaralı adımlarla
