from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_keyboard(is_owner: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = [
        ("✦ Upload Bot", "menu:upload"),
        ("◆ My Bots", "menu:mybots"),
        ("◇ Buy Plan", "menu:buyplan"),
        ("⌁ Referral", "menu:referral"),
        ("$ Saldo", "menu:balance"),
        ("◎ Profile", "menu:profile"),
        ("▣ Plan", "menu:plan"),
        ("◇ Redeem", "menu:redeem"),
        ("? Help", "menu:help"),
        ("» Ping", "menu:ping"),
        ("⌁ Support", "menu:support"),
        ("◌ Runtime", "menu:runtime"),
    ]
    for text, callback in buttons:
        builder.button(text=text, callback_data=callback)
    if is_owner:
        builder.button(text="✧ Owner Panel", callback_data="menu:owner")
    builder.adjust(2, 2, 2, 2, 2, 2)
    return builder.as_markup()


def reply_main_keyboard(is_owner: bool) -> ReplyKeyboardMarkup:
    rows = [
        ["✦ Upload Bot", "◆ My Bots"],
        ["◇ Buy Plan", "⌁ Referral"],
        ["$ Saldo", "◎ Profile"],
        ["▣ Plan", "◇ Redeem"],
        ["? Help", "» Ping"],
        ["⌁ Support", "◌ Runtime"],
    ]
    if is_owner:
        rows.append(["✧ Owner Panel"])
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in rows],
        resize_keyboard=True,
        is_persistent=False,
        input_field_placeholder="Pilih menu...",
    )


def join_keyboard(entries: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for title, link in entries:
        builder.button(text=f"↗ Join {title}", url=link)
    builder.button(text="↻ Cek Akses", callback_data="access:retry")
    builder.adjust(1)
    return builder.as_markup()


def back_keyboard() -> None:
    return None


def yes_no_keyboard(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✓ Ya", callback_data=yes_cb)
    builder.button(text="× Tidak", callback_data=no_cb)
    builder.adjust(2)
    return builder.as_markup()


def buy_plan_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="◆ Pro +1", callback_data="buy:Pro:1")
    builder.button(text="◆ Pro +5", callback_data="buy:Pro:5")
    builder.button(text="✧ VIP +1", callback_data="buy:VIP:1")
    builder.button(text="✧ VIP +5", callback_data="buy:VIP:5")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def owner_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = [
        ("× Banned User", "owner:ban"),
        ("✓ Un Banned", "owner:unban"),
        ("$ Tambah Saldo", "owner:addsaldo"),
        ("◇ Create Redeem", "owner:createredeem"),
        ("▣ Tambah Plan", "owner:addplan"),
        ("✧ Tambah Owner", "owner:addowner"),
        ("◆ Lihat User", "owner:listusers"),
        ("◌ Lihat Bot", "owner:listbots"),
        ("⌫ Hapus Semua Bot", "owner:stopall"),
        ("⟳ Reset Data", "owner:resetdata"),
        ("+ Add To Grup", "owner:addgroup"),
        ("⌁ Kelola Join", "owner:joinmanage"),
    ]
    for text, callback in buttons:
        builder.button(text=text, callback_data=callback)
    builder.adjust(2, 2, 2, 2, 2, 1)
    return builder.as_markup()


def bot_actions_keyboard(bot_id: int, is_running: bool, is_owner: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_running:
        builder.button(text="● Stop", callback_data=f"bot:{bot_id}:stop")
        builder.button(text="↻ Restart", callback_data=f"bot:{bot_id}:restart")
    else:
        builder.button(text="○ Start", callback_data=f"bot:{bot_id}:start")
    builder.button(text="× Delete", callback_data=f"bot:{bot_id}:delete")
    builder.button(text="≡ Log", callback_data=f"bot:{bot_id}:log")
    if is_owner:
        builder.button(text="✧ Owner", callback_data="menu:owner")
    builder.button(text="‹ Kembali", callback_data="menu:home")
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def bots_list_keyboard(bot_ids: list[tuple[int, str]], is_owner: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for bot_id, label in bot_ids:
        builder.button(text=label, callback_data=f"bot:{bot_id}:open")
    if is_owner:
        builder.button(text="✧ Owner", callback_data="menu:owner")
    builder.button(text="‹ Kembali", callback_data="menu:home")
    builder.adjust(1)
    return builder.as_markup()
