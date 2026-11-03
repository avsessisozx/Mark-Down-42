# Reels Gözlemci

Instagram Reels akışını izleyen, kaydıran ve normal bir kullanıcıya görünen her şeyi bir veri setine yazan Windows programı.
Amaç: akışın hangi davranışa nasıl tepki verdiğini gözlemlemek (beğeni, yorum, izlenme, ekranda kalma süresi, reklam…).

- Instagram'a kendi isteklerini göndermez; sayfanın kendi aldığı yanıtları dinler.
- Kendi tarayıcı penceresini açar, girişi sen yaparsın. Oturum kalıcı profilde durur.
- Verileri SQLite'ta tutar, CSV ve JSON olarak dışa aktarır.

Ana hesabın yerine yedek bir hesap kullan: Instagram otomatik kaydırmayı kullanım şartlarına aykırı sayar.

Geliştirme: `pip install -r requirements.txt`, testler: `python -m unittest tests.test_units tests.test_integration`.
