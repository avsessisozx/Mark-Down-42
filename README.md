# Reels Gözlemci

> ⬇️ **İndir:** [Windows (EXE)](https://github.com/avsessisozx/Mark-Down-42/releases/latest/download/ReelsGozlemci.exe)

Instagram Reels akışını izleyen, kaydıran ve normal bir kullanıcıya görünen her şeyi bir veri setine yazan Windows programı.
Amaç: akışın hangi davranışa nasıl tepki verdiğini gözlemlemek (beğeni, yorum, izlenme, ekranda kalma süresi, reklam…).

- Instagram'a kendi isteklerini göndermez; sayfanın kendi aldığı yanıtları dinler.
- Kendi tarayıcı penceresini açar, girişi sen yaparsın. Oturum kalıcı profilde durur.
- Verileri SQLite'ta tutar, CSV ve JSON olarak dışa aktarır.

Ana hesabın yerine yedek bir hesap kullan: Instagram otomatik kaydırmayı kullanım şartlarına aykırı sayar.

Geliştirme: `pip install -r requirements.txt`, testler: `python -m unittest tests.test_units tests.test_integration`.

## Claude bağlantısı

`claude_baglanti/mcp_sunucu.py`, Claude Code'un Gözlemci'yi kullanmasını sağlayan bir MCP sunucusudur: durum, başlat/durdur, izleme listesine ekleme, salt okunur SQL sorgusu, reels'ları türlere ayırma ve tür raporu. Veritabanını değiştirmez; Claude'un tür etiketleri ayrı dosyada (`claude-analiz.sqlite3`) durur. Hesap verisi okunamaz.

```
python -m venv .venv-mcp
.venv-mcp\Scripts\pip install -r claude_baglanti\requirements.txt
claude mcp add reels-gozlemci --scope user -- "<klasör>\.venv-mcp\Scripts\python.exe" "<klasör>\claude_baglanti\mcp_sunucu.py"
```
