# DURUM — Reels Gözlemci

> Claude'un çalışma notu. Yalnızca `durum` dalında durur; `main`'e, README'ye ve sürüm notlarına girmez.
> Yeni sohbette önce bunu oku, repoyu baştan tarama. Her sürümden sonra güncelle.

Son güncelleme: 2026-11-03 · Son sürüm: **v0.3.0** (2026-11-03)

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

- v0.2.0 (2026-11-03): HESAP EKLEME, Instagram İndirici'deki gibi (kullanıcı: "kendi pencerede giriş yaptırmışsın, Instagram İndirici'nin son sürümündeki gibi yap").
  Akış: UI "Hesap ekle (Chrome'dan)" → `/api/login-chrome` tek kullanımlık kod (10 dk) → varsayılan tarayıcıda `https://www.instagram.com/#ii=<port>.<kod>` →
  Chrome eklentisi (`chrome_eklenti/`, Instagram İndirici'ninkiyle AYNI protokol `#ii=`; kullanıcıda zaten kurulu olan eklenti çalışır, ikisini birden kurma) çerezleri
  `POST /api/extension-session` (kökeni `chrome-extension://`, kod tek kullanımlık, CORS ön denemesi `do_OPTIONS`) ile yollar → `state.add_account` (motor çalışıyorsa
  `import_cookies` komutu, değilse `pending_cookies`, Başlat'ta `engine.start(settings, cookies)`) → `engine._apply_cookies` kalıcı profile `add_cookies`, 9 sn sonra doğrulama
  (günlükte "Hesap doğrulandı" / "kabul etmedi"). Yedekler: sessionid yapıştır (`/api/login-cookie`, `session.parse_session_input` = Instagram İndirici'ninki), `/api/extension-open`
  (eklentiyi AppData'ya kopyalar), `/api/logout` (çerezleri siler / `tarayici-*` profillerini siler; `tarayici-bileseni`ne dokunmaz).
  Hata bulundu+düzeltildi: çalışırken hesap eklenince eski "giriş yok" bilgisiyle giriş sayfasına gidiyordu (`_apply_cookies` artık `logged_in`'i yeniler).
  DOĞRULANDI: gerçek eklenti gerçek Chromium'a yüklenip program sunucusuna oturum gönderdi (`tests/test_extension.py`, CI'da ayrı Linux işi `eklenti`; kod yanlışsa reddediliyor,
  giriş sonradan yapılınca otomatik gidiyor); sunucu uçları; motorda çerezle başlama/canlı ekleme/reddedilen çerez/çıkış. Testler: birim 15, uçtan uca 26, eklenti 3.
  DOĞRULANMADI (gerçek Instagram): eklenti çerezleriyle program tarayıcısının girişli sayılması (cihaz çerezleri mid/ig_did/datr ile); Instagram doğrulama (challenge) istemesi;
  eklentinin varsayılan tarayıcı Chrome değilse (Edge) çalışmaması.

- v0.3.0 (2026-11-03): kullanıcının 0.2.0 raporu ve istekleri. Gerçek Instagram'da ÇALIŞTI (rapor): eklentiyle hesap ekleme + doğrulama, otomatik kaydırma (3,5 dk'da 48 izleme, 67 Reel bilgisi, scroll_fail 0), duraklat; beğeni/yorum/müzik/hesap/açıklama geliyor; `plays`/`shares`/`duration` boştu.
  Yapılanlar: (1) Veri klasörü: `paths.documents_dir()` (SHGetKnownFolderPath), `check_writable`, yedek klasör `AppData\ReelsGozlemci\disa-aktarma`, `/api/pick-folder` (PowerShell STA FolderBrowserDialog), `/api/folder-test` (hatanın tam metni), dışa aktarma yazılamazsa yedeğe + uyarı;
  `livewrite.LiveWriter` her izlemeyi `canli-gorunumler.csv`'ye ekler (dosya Excel'de kilitliyse bekletir, klasör değişince hepsini yeniden yazar). (2) UI: İzle/Veriler sekmeleri, Veriler'de klasör + liste + arama, Reel kartında müzik ve 5 sayı. (3) `ui/analiz.html`: geniş etkileşimli Analiz penceresi (`/analiz`, `/api/analysis`), grafikler kendi SVG/HTML kodumuz
  (npm/pip bu ortamda erişilemedi, dış kütüphane YOK): senaryo/oturum karşılaştırma, kalma histogramı, en çok hesap/etiket/müzik (tıkla=filtre), reklam pastası, zaman çizgisi, beğeni–izlenme dağılımı, tablo, Reel kartı. (4) Veri: `send_count` sütunu (eski veritabanı otomatik yükseltilir), izlenme/yen. paylaşım/gönderme için ALAN ADI ADAYLARI
  (`extract._deep_first_int`: play_count, ig_play_count, view_count…; reshare_count/repost_count; send_count/share_count…), video süresi `set_duration_if_missing`, ilk Reel'in bilgisi sayfaya gömülü JSON'dan (`_read_embedded`), rapora "Ham kayıttaki sayaç alanları" listesi (`store.numeric_fields_summary`).
  Klasör hatası: kullanıcının PC'sinde Documents klasörü normal görünüyor (OneDrive yok); masaüstü bağlantısındaki shell Linux VM, Windows yazma hatasını yeniden üretemedi. Şüphe: Windows "Denetimli klasör erişimi" ya da başka. Hata metni artık görünür.
  DOĞRULANDI (sahte site + gerçek Chromium + CI Edge): hepsi; testler birim 26, uçtan uca 29, eklenti 3 (+ Analiz ekranı ekran görüntüsüyle elle kontrol edildi). DOĞRULANMADI: klasör seçme penceresi (Windows PowerShell), Windows'ta yazma hatasının gerçek nedeni, gerçek Instagram'da izlenme/yen. paylaşım/gönderme alanlarının adı (raporun sayaç listesine bak),
  gömülü JSON'dan ilk Reel okuma (gerçek sayfada), Analiz ekranı gerçek veriyle.

## Yarım / sıradaki
- DOĞRULANMADI (gerçek Instagram): (1) kendi pencerede giriş doğrulama kilidine takılıyor mu (eski projede sıfır profil takılmıştı;
  burada kalıcı profil); (2) gerçek Reels sayfasında ArrowDown kaydırıyor mu, adres /reels/<kod>/ oluyor mu; (3) Reels sayfasının
  yüklediği JSON'da beğeni/yorum/izlenme/paylaşım alanları var mı, hangileri gizli; (4) reklam işareti (`ad_id` vb.) gerçek adlarla uyuşuyor mu;
  (5) pencere yerleşimi (sol/sağ) ve `sessionid` çerez kontrolü.
  Kullanıcıdan istenecek: ilk denemede "Raporu kopyala" çıktısı (günlükte `yanıt: N kayıt` satırları ve `responses_with_media` sayısı).
- Doğrulama (challenge) çıkarsa atlatma yardımı YOK; kullanıcı kendi tamamlar.
- İlk iş: kullanıcının yeni raporundaki 'Ham kayıttaki sayaç alanları' listesinden izlenme/yen. paylaşım/gönderme alan adlarını kesinleştir (gerekirse ekrandan okuma yedeği).
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
