from __future__ import annotations

from html import escape
from datetime import timedelta


SMALL_CAPS = str.maketrans({
    "A": "ᴀ", "B": "ʙ", "C": "ᴄ", "D": "ᴅ", "E": "ᴇ", "F": "ꜰ", "G": "ɢ",
    "H": "ʜ", "I": "ɪ", "J": "ᴊ", "K": "ᴋ", "L": "ʟ", "M": "ᴍ", "N": "ɴ",
    "O": "ᴏ", "P": "ᴘ", "Q": "Q", "R": "ʀ", "S": "s", "T": "ᴛ", "U": "ᴜ",
    "V": "ᴠ", "W": "ᴡ", "X": "x", "Y": "ʏ", "Z": "ᴢ",
})

BOLD_SANS = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇",
)


def small_caps(value: str) -> str:
    return value.translate(SMALL_CAPS)


def bold_sans(value: str) -> str:
    return value.translate(BOLD_SANS)


def title(icon: str, value: str) -> str:
    return f"{icon} <b>{bold_sans(value.upper())}</b>"


def format_seconds(seconds: int) -> str:
    delta = timedelta(seconds=max(0, seconds))
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def format_money(amount: int) -> str:
    return f"{amount:,}".replace(",", ".")


def progress_bar(current: int, total: int, width: int = 12) -> str:
    total = max(total, 1)
    current = max(0, min(current, total))
    filled = round(width * current / total)
    return "▰" * filled + "▱" * (width - filled)


def loading_message(title: str, current: int, total: int) -> str:
    percent = int(max(0, min(current, total)) / max(total, 1) * 100)
    return (
        f"◌ <b>{bold_sans(escape(title.upper()))}</b>\n\n"
        f"<code>{progress_bar(current, total)}</code> <b>{percent}%</b>\n"
        "╰ tunggu sebentar, lagi disiapkan..."
    )


def start_message(bot_name: str, bot_version: str, user_name: str, plan: str, bot_status: str, balance: int, owners: list[str], plan_expiry: str = "Selamanya") -> str:
    owner_text = ", ".join(escape(owner) for owner in owners) or "WanzHosting Team"
    return (
        f"✦ {escape(bot_name)} 〄 <b>{escape(bot_version)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"┊ <b>𝗪𝗘𝗟𝗖𝗢𝗠𝗘, {escape(user_name)}</b>\n"
        "┌────────────────────\n"
        f"│ ◇ 𝗣𝗟𝗔𝗡      ┃ <b>{escape(plan)}</b>\n"
        f"│ ◌ 𝗔𝗞𝗧𝗜𝗙    ┃ <b>{escape(plan_expiry)}</b>\n"
        f"│ ● 𝗦𝗧𝗔𝗧𝗨𝗦   ┃ <b>{escape(bot_status)}</b>\n"
        f"│ $ 𝗦𝗔𝗟𝗗𝗢    ┃ <b>{format_money(balance)}$</b>\n"
        f"│ ✧ 𝗣𝗢𝗪𝗘𝗥𝗘𝗗  ┃ <b>{owner_text}</b>\n"
        "└────────────────────\n\n"
        "Pilih menu di bawah kalau mau mulai."
    )


def access_message() -> str:
    return (
        "⌁ <b>AKSES TERBATAS</b>\n\n"
        "Join semua channel/grup resmi di bawah, lalu tekan <b>↻ Cek Akses</b>."
    )


def help_message() -> str:
    return (
        f"{title('?', 'Bantuan')}\n\n"
        "✦ <b>Upload Bot</b> — kirim file <code>.py</code> atau <code>.zip</code>\n"
        "◆ <b>My Bots</b> — kelola bot dan status runtime\n"
        "◇ <b>Buy Plan</b> — beli slot hosting sesuai kebutuhan\n"
        "⌁ <b>Referral</b> — bonus <b>100$</b> tiap user baru\n"
        "▣ <b>Redeem</b> — tukarkan kode saldo\n\n"
        "└ Pastikan file punya entry point Python yang valid."
    )
