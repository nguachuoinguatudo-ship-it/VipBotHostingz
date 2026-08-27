# VipBotHostingz

Telegram bot hosting panel dengan:

- `/start` modern dengan status, saldo, plan, dan referral
- upload bot via `.py` atau `.zip`
- hosting bot Python (`.py` atau `.zip`)
- buy plan `pro` dan `vip` dengan masa aktif 30 hari
- referral, redeem, saldo, profile, help, ping, support, runtime
- panel owner untuk ban, unban, tambah saldo, tambah owner, tambah plan, lihat user, lihat bot, dan reset bot aktif

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
