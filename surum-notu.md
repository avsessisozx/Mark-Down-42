**Bu sürümde (0.5.0):**

- **Ekran sayaçları artık okunuyor:** Instagram sayaçlarda etiket vermediği için 0.4.0 hiçbir sayıyı yazamıyordu. Sayılar artık ekrandaki sıraya göre eşleniyor (beğeni, yorum, repost, gönderme). Sıra, Instagram'ın verdiği beğeni ve yorum sayısıyla doğrulanıyor; tutmazsa hiçbir şey yazılmıyor.
- **Yeni veri: repost sayısı.** Instagram'ın yanıtında yok, yalnız ekrandan okunuyor.
- **Düzeltme:** Instagram'ın `reshare_count` alanı aslında **gönderme** (kâğıt uçak) sayısı. Eski sürümler bunu "yeniden paylaşım" sütununa yazıyordu. Program ilk açılışta eski kayıtları doğru sütuna taşır.
- **Claude bağlantısı (isteğe bağlı):** `claude_baglanti/mcp_sunucu.py`. Claude Code bu bağlantıyla programı başlatıp durdurabiliyor, veriyi salt okunur sorgulayabiliyor ve reels'ları türlere ayırıp tür raporu çıkarabiliyor. Hesap verisi (takipçi listesi vb.) bu bağlantıyla okunamaz.

İzlenme sayısı Reels ekranında gösterilmediği için çoğu reel'de hâlâ boş.

**Kurulum:** `ReelsGozlemci.exe` dosyasını indirip çift tıkla. Windows uyarı verirse **Ek bilgi → Yine de çalıştır**.
