# VipBotHostingz

Telegram bot hosting panel dengan:

- `/start` modern dengan status, saldo, plan, dan referral
- upload bot via `.py` atau `.zip`
- hosting bot Python (`.py` atau `.zip`)
- buy plan `pro` dan `vip` dengan masa aktif 30 hari
- referral, redeem, saldo, profile, help, ping, support, runtime
- panel owner untuk ban, unban, tambah saldo, tambah owner, tambah plan, lihat user, lihat bot, reset semua data
- lifecycle ala panel: start, stop, restart, log persisten, deteksi crash, dan auto-restart
- batas memory/CPU dasar per proses serta rotasi log runtime

## Setup

1. Copy `.env.example` ke `.env`
2. Isi `BOT_TOKEN`, `BOT_USERNAME`, dan link support/join
3. Install dependency:

```bash
pip install -r requirements.txt
```

4. Jalankan:

```bash
python main.py
```

## Catatan

- Bot ini pakai SQLite lokal.
- Upload `.zip` akan diekstrak otomatis.
- Jika ada `requirements.txt`, dependency Python dipasang ke folder `vendor/` bot tersebut.
- Pembelian plan menambah kapasitas hosting dan memperpanjang masa aktif 30 hari.
- Untuk fitur wajib join, isi data lewat panel owner atau edit tabel `required_chats`.
- `AUTO_RESTART=true` menghidupkan kembali bot saat crash.
- `BOT_MEMORY_LIMIT_MB=512` membatasi memory proses; minimum aman yang diterima adalah 128 MB.
- `BOT_CPU_LIMIT_SECONDS=0` berarti tanpa batas CPU; isi angka positif jika ingin membatasi waktu CPU.
- Ini adalah panel hosting Python ringan, bukan pengganti isolasi Docker/Pterodactyl penuh untuk kode yang tidak dipercaya.
