from __future__ import annotations

import asyncio
import io
import secrets
import shutil
import string
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Callable

from aiogram import F, Router, types
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from .config import Settings
from .db import Database, utc_now
from .hosting import HostingManager, tail_log
from .keyboards import (
    back_keyboard,
    bot_actions_keyboard,
    buy_plan_keyboard,
    bots_list_keyboard,
    join_keyboard,
    main_keyboard,
    owner_keyboard,
    reply_main_keyboard,
)
from .texts import access_message, copy_code, format_money, format_seconds, help_message, loading_message, quote_text, start_message, title


router = Router()
START_THUMBNAIL = "https://foxxy-free-imghosting.vercel.app/foxxy-ltjpcgm8.jpg"


class AppContext:
    def __init__(self, settings: Settings, db: Database, hosting: HostingManager, bot_username: str) -> None:
        self.settings = settings
        self.db = db
        self.hosting = hosting
        self.bot_username = bot_username
        self.started_at = time.monotonic()

    def referral_link(self, user_id: int) -> str:
        if not self.bot_username:
            return "Set BOT_USERNAME di .env"
        return f"https://t.me/{self.bot_username}?start=ref_{user_id}"


class ReplyCallback:
    def __init__(self, message: Message, user, data: str) -> None:
        self.message = message
        self.from_user = user
        self.bot = message.bot
        self.data = data

    async def answer(self, *args, **kwargs) -> None:
        return None


REPLY_MENU_ACTIONS = {
    "✦ Upload Bot": "menu:upload",
    "◆ My Bots": "menu:mybots",
    "◇ Buy Plan": "menu:buyplan",
    "⌁ Referral": "menu:referral",
    "$ Saldo": "menu:balance",
    "◎ Profile": "menu:profile",
    "▣ Plan": "menu:plan",
    "◇ Redeem": "menu:redeem",
    "? Help": "menu:help",
    "» Ping": "menu:ping",
    "⌁ Support": "menu:support",
    "◌ Runtime": "menu:runtime",
    "✧ Owner Panel": "menu:owner",
}


def is_owner(settings: Settings, user_id: int) -> bool:
    return user_id in settings.owner_ids


def plan_expiry_text(user) -> str:
    value = user["plan_expires_at"]
    if not value:
        return "Selamanya"
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%d-%m-%Y")
    except ValueError:
        return "Tidak tersedia"


class AccessGateMiddleware(BaseMiddleware):
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)

        if is_owner(self.ctx.settings, user.id):
            return await handler(event, data)

        user_row = await self.ctx.db.get_user(user.id)
        if user_row and int(user_row["banned"]) == 1:
            if isinstance(event, CallbackQuery):
                await event.answer("Kamu dibanned.", show_alert=True)
            else:
                await event.answer("Kamu dibanned dari bot ini.")
            return

        allow_start = isinstance(event, Message) and (event.text or "").startswith("/start")
        allow_retry = isinstance(event, CallbackQuery) and event.data == "access:retry"
        if allow_start or allow_retry:
            return await handler(event, data)

        missing = await missing_required_chats(event.bot, self.ctx.db, user.id)
        if missing:
            if isinstance(event, CallbackQuery):
                try:
                    await event.message.edit_text(access_message(), reply_markup=join_keyboard(missing))
                except Exception:
                    await event.message.answer(access_message(), reply_markup=join_keyboard(missing))
                await event.answer()
            else:
                await event.answer(access_message(), reply_markup=join_keyboard(missing))
            return

        return await handler(event, data)


async def required_chat_entries(db: Database) -> list[tuple[str, str]]:
    rows = await db.list_required_chats()
    entries: list[tuple[str, str]] = []
    for row in rows:
        entries.append((row["title"], row["invite_link"]))
    return entries


async def has_access(message_or_user: Message | types.User, db: Database, settings: Settings) -> bool:
    user_id = message_or_user.from_user.id if isinstance(message_or_user, Message) else message_or_user.id
    if user_id in settings.owner_ids:
        return True
    rows = await db.list_required_chats()
    if not rows:
        return True
    return False


async def missing_required_chats(bot, db: Database, user_id: int) -> list[tuple[str, str]]:
    rows = await db.list_required_chats()
    missing: list[tuple[str, str]] = []
    for row in rows:
        try:
            member = await bot.get_chat_member(row["chat_id"], user_id)
            if member.status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
                continue
        except Exception:
            pass
        missing.append((row["title"], row["invite_link"]))
    return missing


async def safe_send_home(message: Message, ctx: AppContext) -> None:
    user = message.from_user
    record = await ctx.db.get_user(user.id)
    if record is None:
        return
    running = await ctx.db.count_user_bots(user.id)
    text = start_message(
        ctx.settings.bot_name,
        ctx.settings.bot_version,
        user.full_name,
        f"{record['plan']} · {record['plan_limit']} slot",
        f"Running {running} Bot" if running else "Idle",
        int(record["balance"]),
        ctx.settings.owner_names,
        plan_expiry_text(record),
    )
    markup = reply_main_keyboard(is_owner(ctx.settings, user.id))
    try:
        await message.answer_photo(START_THUMBNAIL, caption=text, reply_markup=markup)
    except Exception:
        await message.answer(text, reply_markup=markup)


@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, ctx: AppContext) -> None:
    user = message.from_user
    await ctx.db.ensure_user(user.id, user.username, user.full_name)
    await ctx.db.update_last_seen(user.id)
    await state.clear()

    if command.args and command.args.startswith("ref_"):
        try:
            referrer_id = int(command.args.removeprefix("ref_"))
            if referrer_id != user.id:
                referred = await ctx.db.get_user(user.id)
                if referred and referred["referred_by"] is None:
                    await ctx.db.ensure_user(referrer_id, None, str(referrer_id))
                    await ctx.db.conn.execute(
                        "UPDATE users SET referred_by=? WHERE user_id=?",
                        (referrer_id, user.id),
                    )
                    await ctx.db.add_balance(referrer_id, 100)
                    await ctx.db.conn.execute(
                        "UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id=?",
                        (referrer_id,),
                    )
                    await ctx.db.conn.commit()
                    try:
                        await message.bot.send_message(
                            referrer_id,
                            f"✦ Referral masuk\n\n┊ {user.full_name} bergabung lewat link kamu.\n$ Bonus: +100$",
                        )
                    except Exception:
                        pass
        except ValueError:
            pass

    if not is_owner(ctx.settings, user.id):
        missing = await missing_required_chats(message.bot, ctx.db, user.id)
        if missing:
            await message.answer(access_message(), reply_markup=join_keyboard(missing))
            return

    await safe_send_home(message, ctx)


@router.callback_query(F.data == "access:retry")
async def retry_access(callback: CallbackQuery, ctx: AppContext) -> None:
    user = callback.from_user
    missing = await missing_required_chats(callback.bot, ctx.db, user.id)
    if missing:
        await callback.message.edit_text(access_message(), reply_markup=join_keyboard(missing))
    else:
        await callback.message.edit_text(
            start_message(
                ctx.settings.bot_name,
                ctx.settings.bot_version,
                user.full_name,
                f"{(await ctx.db.get_user(user.id))['plan']} · {(await ctx.db.get_user(user.id))['plan_limit']} slot",
                "Ready",
                int((await ctx.db.get_user(user.id))["balance"]),
                ctx.settings.owner_names,
                plan_expiry_text(await ctx.db.get_user(user.id)),
            ),
            reply_markup=main_keyboard(is_owner(ctx.settings, user.id)),
        )
    await callback.answer()


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, ctx: AppContext) -> None:
    user = callback.from_user
    record = await ctx.db.get_user(user.id)
    running = await ctx.db.count_user_bots(user.id)
    await callback.message.edit_text(
        start_message(
            ctx.settings.bot_name,
            ctx.settings.bot_version,
            user.full_name,
            f"{record['plan']} · {record['plan_limit']} slot",
            f"Running {running} Bot" if running else "Idle",
            int(record["balance"]),
            ctx.settings.owner_names,
            plan_expiry_text(record),
        ),
        reply_markup=main_keyboard(is_owner(ctx.settings, user.id)),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:upload")
async def menu_upload(callback: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await state.update_data(action="upload_bot")
    await callback.message.edit_text(
        f"{title('✦', 'Upload Bot')}\n\n"
        "Kirim file bot kamu sekarang:\n"
        "▫️ Python: <code>.py</code> / <code>.zip</code>\n"
        "ZIP akan diekstrak otomatis dan mencari file Python utama.",
        reply_markup=None,
    )
    await callback.answer()


@router.callback_query(F.data == "menu:mybots")
async def menu_mybots(callback: CallbackQuery, ctx: AppContext) -> None:
    bots = await ctx.db.list_user_bots(callback.from_user.id)
    if not bots:
        await callback.message.edit_text(f"{title('○', 'My Bots')}\n\nBelum ada bot yang aktif.", reply_markup=back_keyboard())
        await callback.answer()
        return

    lines = [title("◆", "My Bots"), "╰ Pilih bot yang mau dikelola", ""]
    buttons: list[tuple[int, str]] = []
    for bot in bots[:20]:
        status = "Running" if bot["status"] == "running" else "Stopped"
        lines.append(f"{('●' if status == 'Running' else '○')} <b>#{bot['id']} {bot['name']}</b> · {status}")
        buttons.append((int(bot["id"]), f"#{bot['id']} {bot['name']}"))
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=bots_list_keyboard(buttons, is_owner(ctx.settings, callback.from_user.id)),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:buyplan")
async def menu_buyplan(callback: CallbackQuery) -> None:
    text = (
        f"{title('◇', 'Hosting Plans')}\n\n"
        "◆ <b>PRO</b> — 200$ / 2 slot bot\n"
        "✧ <b>VIP</b> — 1.000$ / 5 slot bot\n\n"
        "Beli +1 atau +5. Setiap pembelian menambah slot hosting kamu."
    )
    await callback.message.edit_text(text, reply_markup=buy_plan_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def buy_plan(callback: CallbackQuery, ctx: AppContext) -> None:
    parts = callback.data.split(":")
    plan_name = parts[1]
    quantity = int(parts[2]) if len(parts) > 2 else 1
    plan = await ctx.db.get_plan(plan_name)
    user = await ctx.db.get_user(callback.from_user.id)
    if plan is None or user is None:
        await callback.answer("Plan tidak ditemukan", show_alert=True)
        return
    price = int(plan["price"]) * quantity
    if int(user["balance"]) < price:
        await callback.answer("Saldo tidak cukup", show_alert=True)
        return
    await callback.message.edit_text(loading_message("Pembelian plan", 1, 3))
    await asyncio.sleep(0.25)
    await callback.message.edit_text(loading_message("Pembelian plan", 2, 3))
    await asyncio.sleep(0.35)
    success, remaining, _, new_limit = await ctx.db.purchase_plan(callback.from_user.id, plan_name, quantity)
    if not success:
        await callback.message.edit_text("❌ Pembelian gagal atau saldo berubah.", reply_markup=back_keyboard())
        await callback.answer("Gagal", show_alert=True)
        return
    await callback.message.edit_text(
        "✓ <b>PEMBELIAN BERHASIL</b>\n\n"
        f"◇ Plan: <b>{plan['plan_name']} ×{quantity}</b>\n"
        f"◆ Kapasitas: <b>{new_limit} bot</b>\n"
        f"$ Terpakai: <b>{format_money(price)}$</b>\n"
        f"$ Sisa saldo: <b>{format_money(remaining)}$</b>",
        reply_markup=back_keyboard(),
    )
    await callback.answer("Berhasil")


@router.callback_query(F.data == "menu:referral")
async def menu_referral(callback: CallbackQuery, ctx: AppContext) -> None:
    link = ctx.referral_link(callback.from_user.id)
    text = (
        "⌁ <b>REFERRAL PROGRAM</b>\n\n"
        f"↗ Link kamu:\n{copy_code(link)}\n\n"
        "$ Setiap user baru dari link kamu memberi bonus <b>100$</b>."
    )
    await callback.message.edit_text(text, reply_markup=back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:balance")
async def menu_balance(callback: CallbackQuery, ctx: AppContext) -> None:
    user = await ctx.db.get_user(callback.from_user.id)
    bots = await ctx.db.count_user_bots(callback.from_user.id)
    await callback.message.edit_text(
        f"{title('$', 'Wallet Overview')}\n\n"
        f"$ Saldo: <b>{format_money(int(user['balance']))}$</b>\n"
        f"◇ Plan: <b>{user['plan']}</b>\n"
        f"◆ Bot aktif: <b>{bots}</b>",
        reply_markup=back_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:profile")
async def menu_profile(callback: CallbackQuery, ctx: AppContext) -> None:
    user = await ctx.db.get_user(callback.from_user.id)
    rows = await ctx.db.count_user_bots(callback.from_user.id)
    text = (
        f"{title('◎', 'Profile')}\n\n"
        f"# ID: <code>{callback.from_user.id}</code>\n"
        f"┊ Nama: <b>{callback.from_user.full_name}</b>\n"
        f"↗ Username: @{callback.from_user.username or '-'}\n"
        f"◇ Plan: <b>{user['plan']}</b>\n"
        f"$ Saldo: <b>{format_money(int(user['balance']))}$</b>\n"
        f"⌁ Referral: <b>{int(user['referrals_count'])}</b>\n"
        f"◆ Bot aktif: <b>{rows}</b>"
    )
    await callback.message.edit_text(text, reply_markup=back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:plan")
async def menu_plan(callback: CallbackQuery, ctx: AppContext) -> None:
    plans = await ctx.db.list_plans()
    lines = [title("◇", "Daftar Plan"), ""]
    for plan in plans:
        lines.append(
            f"▫️ <b>{plan['plan_name']}</b> — {format_money(int(plan['price']))}$ / {plan['max_bots']} slot"
        )
    user = await ctx.db.get_user(callback.from_user.id)
    lines.append("")
    lines.append(f"\n✓ Plan aktif: <b>{user['plan']}</b> · kapasitas <b>{user['plan_limit']} bot</b>")
    await callback.message.edit_text("\n".join(lines), reply_markup=buy_plan_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:redeem")
async def menu_redeem(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(action="redeem_code")
    await callback.message.edit_text(
        f"{title('◇', 'Redeem')}\n\nKirim kode redeem kamu sekarang.",
        reply_markup=back_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:help")
async def menu_help(callback: CallbackQuery) -> None:
    await callback.message.edit_text(help_message(), reply_markup=back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:support")
async def menu_support(callback: CallbackQuery, ctx: AppContext) -> None:
    await callback.message.edit_text(
        f"{title('⌁', 'Support')}\n\n{ctx.settings.support_text}\n\n↗ <code>{ctx.settings.support_url}</code>",
        reply_markup=back_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:runtime")
async def menu_runtime(callback: CallbackQuery, ctx: AppContext) -> None:
    uptime = int(time.monotonic() - ctx.started_at)
    await callback.message.edit_text(
        f"{title('◌', 'System Runtime')}\n\n"
        f"⌁ Uptime: <b>{format_seconds(uptime)}</b>\n"
        f"● Bot aktif: <b>{len(ctx.hosting.processes)}</b>",
        reply_markup=back_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:ping")
async def menu_ping(callback: CallbackQuery) -> None:
    started = time.perf_counter()
    await callback.answer("◌ mengecek koneksi...")
    elapsed = int((time.perf_counter() - started) * 1000)
    try:
        await callback.message.edit_text(
            f"Pong! {elapsed} ms",
            reply_markup=back_keyboard(),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "menu:owner")
async def menu_owner(callback: CallbackQuery, ctx: AppContext) -> None:
    if not is_owner(ctx.settings, callback.from_user.id):
        await callback.answer("Akses ditolak", show_alert=True)
        return
    await callback.message.edit_text(f"{title('✧', 'Menu Owner')}\n\nKelola user, plan, saldo, dan bot yang sedang berjalan.", reply_markup=owner_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("owner:"))
async def owner_action(callback: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    if not is_owner(ctx.settings, callback.from_user.id):
        await callback.answer("Akses ditolak", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]
    if action == "listusers":
        users = await ctx.db.get_users(20)
        total = await ctx.db.count_users()
        lines = [f"Total user: {total}", ""]
        for row in users:
            lines.append(
                f"{row['user_id']} | {row['full_name']} | {row['plan']} | {format_money(int(row['balance']))}$"
            )
        await callback.message.edit_text("\n".join(lines), reply_markup=owner_keyboard())
        await callback.answer()
        return
    if action == "listbots":
        bots = await ctx.db.list_all_bots()
        if not bots:
            await callback.message.edit_text("Belum ada bot.", reply_markup=owner_keyboard())
            await callback.answer()
            return
        lines = ["Semua bot:"]
        for row in bots[:30]:
            lines.append(
                f"#{row['id']} | owner {row['owner_id']} | {row['name']} | {row['status']}"
            )
        await callback.message.edit_text("\n".join(lines), reply_markup=owner_keyboard())
        await callback.answer()
        return
    await state.update_data(action=f"owner_{action}")
    prompts = {
        "ban": "Kirim user_id yang ingin dibanned.",
        "unban": "Kirim user_id yang ingin di-unban.",
        "addsaldo": "Kirim format: user_id|amount",
        "createredeem": "Kirim <code>amount|uses</code> untuk kode acak, atau <code>code|amount|uses</code> untuk kode custom.",
        "addplan": "Kirim format: name|price|max_bots",
        "addowner": "Kirim format: user_id|label",
        "addgroup": "Kirim format: chat_id|title|invite_link|kind",
        "joinmanage": "Kirim `list`, `delete:chat_id`, atau `add:chat_id|title|invite_link|kind`.",
    }
    if action == "stopall":
        await ctx.hosting.stop_all()
        await callback.message.edit_text("Semua bot aktif sudah dihentikan.", reply_markup=owner_keyboard())
        await callback.answer("Selesai")
        return
    if action == "resetdata":
        bots = await ctx.db.list_all_bots()
        for bot in bots:
            await ctx.hosting.delete_bot(int(bot["id"]))
        await ctx.db.reset_data()
        await callback.message.edit_text("⟳ <b>DATA BERHASIL DIRESET</b>\n\nUser, bot, redeem, dan sesi sudah dibersihkan.", reply_markup=owner_keyboard())
        await callback.answer("Data direset")
        return
    await callback.message.edit_text(prompts.get(action, "Kirim data sesuai kebutuhan."), reply_markup=back_keyboard())
    await callback.answer()


async def _handle_owner_text(message: Message, action: str, ctx: AppContext) -> str:
    text = message.text or ""
    if action == "owner_ban":
        target = int(text.strip())
        await ctx.db.ban_user(target, True)
        return f"User {target} berhasil dibanned."
    if action == "owner_unban":
        target = int(text.strip())
        await ctx.db.ban_user(target, False)
        return f"User {target} berhasil di-unban."
    if action == "owner_addsaldo":
        target, amount = [part.strip() for part in text.split("|", 1)]
        await ctx.db.add_balance(int(target), int(amount))
        return f"Saldo user {target} ditambah {amount}$."
    if action == "owner_createredeem":
        parts = [part.strip() for part in text.split("|")]
        if len(parts) == 2:
            code = "RDM-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
            amount, uses = parts
        elif len(parts) == 3:
            code, amount, uses = parts
            if code.lower() in {"random", "auto", "acak"}:
                code = "RDM-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
        else:
            raise ValueError("gunakan amount|uses atau code|amount|uses")
        await ctx.db.create_redeem_code(code, int(amount), int(uses), message.from_user.id)
        return f"◇ Kode redeem dibuat:\n\n{copy_code(code)}\n$ Nilai: {amount}$ · Pemakaian: {uses}x"
    if action == "owner_addplan":
        name, price, max_bots = [part.strip() for part in text.split("|", 2)]
        await ctx.db.conn.execute(
            """
            INSERT INTO plans (plan_name, price, max_bots, description)
            VALUES (?, ?, ?, '')
            ON CONFLICT(plan_name) DO UPDATE SET price=excluded.price, max_bots=excluded.max_bots
            """,
            (name, int(price), int(max_bots)),
        )
        await ctx.db.conn.commit()
        return f"Plan {name} disimpan."
    if action == "owner_addowner":
        target, label = [part.strip() for part in text.split("|", 1)]
        await ctx.db.add_owner(int(target), label)
        ctx.settings.owner_ids.add(int(target))
        return f"Owner {label} ditambahkan."
    if action == "owner_addgroup":
        chat_id, title, invite_link, kind = [part.strip() for part in text.split("|", 3)]
        await ctx.db.add_required_chat(chat_id, title, invite_link, kind)
        return f"Requirement join {title} disimpan."
    if action == "owner_joinmanage":
        if text.strip() == "list":
            rows = await ctx.db.list_required_chats()
            if not rows:
                return "Belum ada requirement join."
            return "\n".join([f"{row['chat_id']} - {row['title']} - {row['invite_link']}" for row in rows])
        if text.startswith("delete:"):
            chat_id = text.split(":", 1)[1].strip()
            await ctx.db.delete_required_chat(chat_id)
            return f"Requirement {chat_id} dihapus."
        if text.startswith("add:"):
            payload = text.split(":", 1)[1]
            chat_id, title, invite_link, kind = [part.strip() for part in payload.split("|", 3)]
            await ctx.db.add_required_chat(chat_id, title, invite_link, kind)
            return f"Requirement {title} disimpan."
    raise ValueError("Format tidak valid")


@router.message(F.document)
async def on_document(message: Message, state: FSMContext, ctx: AppContext) -> None:
    user = message.from_user
    await ctx.db.ensure_user(user.id, user.username, user.full_name)
    data = await state.get_data()
    action = data.get("action")
    if action != "upload_bot":
        return

    if not is_owner(ctx.settings, user.id):
        missing = await missing_required_chats(message.bot, ctx.db, user.id)
        if missing:
            await message.answer(access_message(), reply_markup=join_keyboard(missing))
            return

    user_row = await ctx.db.get_user(user.id)
    running = await ctx.db.count_user_bots(user.id)
    if running >= int(user_row["plan_limit"]):
        await message.answer("Limit plan kamu sudah penuh.", reply_markup=back_keyboard())
        await state.clear()
        return

    doc = message.document
    if doc is None:
        return
    loading = await message.answer(loading_message("Menyiapkan upload", 1, 6))
    try:
        await asyncio.sleep(0.2)
        file = await message.bot.get_file(doc.file_id)
        await loading.edit_text(loading_message("Mengunduh file", 2, 6))
        await asyncio.sleep(0.2)
        buffer = io.BytesIO()
        await message.bot.download_file(file.file_path, destination=buffer)
        payload = buffer.getvalue()
    except Exception as exc:
        await loading.edit_text(loading_message("Upload selesai", 6, 6))
        await message.answer(
            f"× <b>UPLOAD GAGAL</b>\n\n<pre>{escape(str(exc)[-3500:])}</pre>",
        )
        await state.clear()
        return

    try:
        await loading.edit_text(loading_message("Mengekstrak dan menyimpan", 3, 6))
        await asyncio.sleep(0.2)
        bot_id, source_root, entry_point, kind = await ctx.hosting.save_uploaded_bot(user.id, doc.file_name or "bot.py", payload)
        await loading.edit_text(loading_message("Menginstall requirements", 4, 6))
        await ctx.hosting.start_bot(bot_id, source_root, entry_point)
        await loading.edit_text(loading_message("Menjalankan bot", 5, 6))
        await asyncio.sleep(0.2)
        await loading.edit_text(loading_message("Upload selesai", 6, 6))
        await message.answer(
            f"{title('✓', 'Deployment Berhasil')}\n\n"
            f"# ID: <code>{bot_id}</code>\n"
            f"▣ Runtime: <b>{kind.upper()}</b>\n"
            "● Status: <b>RUNNING</b>",
            reply_markup=back_keyboard(),
        )
    except Exception as exc:
        await loading.edit_text(loading_message("Upload selesai", 6, 6))
        await message.answer(
            f"× <b>UPLOAD GAGAL</b>\n\n<pre>{escape(str(exc)[-3500:])}</pre>\n\n"
            "Buka menu bot lalu cek log untuk detailnya.",
            reply_markup=back_keyboard(),
        )
    finally:
        await state.clear()


@router.message(F.text.in_((
    "✦ Upload Bot", "◆ My Bots", "◇ Buy Plan", "⌁ Referral", "$ Saldo", "◎ Profile",
    "▣ Plan", "◇ Redeem", "? Help", "» Ping", "⌁ Support", "◌ Runtime",
    "✧ Owner Panel",
)))
async def reply_menu_priority(message: Message, state: FSMContext, ctx: AppContext) -> None:
    action = REPLY_MENU_ACTIONS[message.text or ""]
    if action == "menu:owner" and not is_owner(ctx.settings, message.from_user.id):
        await message.answer("× Akses ditolak.")
        return
    placeholder = await message.answer(loading_message("Menyiapkan", 1, 3))
    callback = ReplyCallback(placeholder, message.from_user, action)
    handlers = {
        "menu:upload": menu_upload,
        "menu:mybots": menu_mybots,
        "menu:buyplan": menu_buyplan,
        "menu:referral": menu_referral,
        "menu:balance": menu_balance,
        "menu:profile": menu_profile,
        "menu:plan": menu_plan,
        "menu:redeem": menu_redeem,
        "menu:help": menu_help,
        "menu:ping": menu_ping,
        "menu:support": menu_support,
        "menu:runtime": menu_runtime,
        "menu:owner": menu_owner,
    }
    handler = handlers[action]
    if action == "menu:upload":
        await handler(callback, state, ctx)
    elif action == "menu:redeem":
        await handler(callback, state)
    elif action in {"menu:mybots", "menu:referral", "menu:balance", "menu:profile", "menu:plan", "menu:support", "menu:runtime", "menu:owner"}:
        await handler(callback, ctx)
    else:
        await handler(callback)
        return

@router.message(F.text)
async def on_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    user = message.from_user
    await ctx.db.ensure_user(user.id, user.username, user.full_name)
    data = await state.get_data()
    action = data.get("action")
    if not action:
        return

    if action == "redeem_code":
        code = message.text.strip()
        amount = await ctx.db.use_redeem_code(code)
        if amount is None:
            await message.answer("Kode redeem tidak ditemukan.", reply_markup=back_keyboard())
        elif amount == -1:
            await message.answer("Kode redeem sudah habis dipakai.", reply_markup=back_keyboard())
        else:
            await ctx.db.add_balance(user.id, amount)
            await message.answer(f"Redeem berhasil. Saldo +{amount}$.", reply_markup=back_keyboard())
        await state.clear()
        return

    if action == "upload_bot":
        await message.answer("Kirim file, bukan teks.", reply_markup=back_keyboard())
        return

    if action.startswith("owner_"):
        if not is_owner(ctx.settings, user.id):
            await message.answer("Akses ditolak.", reply_markup=back_keyboard())
            await state.clear()
            return
        try:
            result = await _handle_owner_text(message, action, ctx)
            await message.answer(result, reply_markup=owner_keyboard())
        except Exception as exc:
            await message.answer(f"Data tidak valid: {exc}", reply_markup=owner_keyboard())
        finally:
            await state.clear()
        return


@router.callback_query(F.data.startswith("bot:"))
async def bot_callback(callback: CallbackQuery, ctx: AppContext) -> None:
    _, bot_id_text, action = callback.data.split(":")
    bot_id = int(bot_id_text)
    bot_row = await ctx.db.get_bot(bot_id)
    if bot_row is None or bot_row["owner_id"] != callback.from_user.id and not is_owner(ctx.settings, callback.from_user.id):
        await callback.answer("Akses ditolak", show_alert=True)
        return

    if action == "open":
        status = {
            "running": "Running",
            "crashed": "Crashed",
        }.get(bot_row["status"], "Stopped")
        await callback.message.edit_text(
            f"{title('◆', f'Bot #{bot_id}')}\n\n"
            f"┊ Nama: <b>{bot_row['name']}</b>\n"
            f"┊ Tipe: <b>{bot_row['kind']}</b>\n"
            f"┊ Status: <b>{status}</b>\n"
            f"┊ Entry: <code>{bot_row['entry_point']}</code>",
            reply_markup=bot_actions_keyboard(
                bot_id,
                bot_row["status"] == "running",
                is_owner(ctx.settings, callback.from_user.id),
            ),
        )
        await callback.answer()
        return

    if action == "log":
        log_path = Path(bot_row["source_path"]) / "runtime.log"
        log = tail_log(log_path, lines=120)
        await callback.message.edit_text(
            f"{title('≡', f'Log Bot #{bot_id}')}\n"
            "┊ Log tersimpan otomatis dan tidak dihapus saat refresh.\n\n"
            f"<pre>{escape(log[-3500:])}</pre>",
            reply_markup=bot_actions_keyboard(
                bot_id,
                bot_row["status"] == "running",
                is_owner(ctx.settings, callback.from_user.id),
            ),
        )
        await callback.answer()
        return

    if action == "start":
        await ctx.hosting.start_bot(bot_id, Path(bot_row["source_path"]), Path(bot_row["entry_point"]))
        await callback.answer("Bot dijalankan")
    elif action == "stop":
        await ctx.hosting.stop_bot(bot_id)
        await callback.answer("Bot dihentikan")
    elif action == "restart":
        await ctx.hosting.stop_bot(bot_id)
        await ctx.hosting.start_bot(bot_id, Path(bot_row["source_path"]), Path(bot_row["entry_point"]))
        await callback.answer("Bot direstart")
    elif action == "delete":
        await ctx.hosting.delete_bot(bot_id)
        await callback.answer("Bot dihapus")
        await callback.message.edit_text("Bot sudah dihapus.", reply_markup=back_keyboard())
        return
    refreshed = await ctx.db.get_bot(bot_id)
    if refreshed is None:
        await callback.message.edit_text("Bot sudah dihapus.", reply_markup=back_keyboard())
        return
    await callback.message.edit_text(
        f"{title('◆', f'Bot #{bot_id}')}\n\nStatus: <b>{'Running' if refreshed['status'] == 'running' else 'Stopped'}</b>",
        reply_markup=bot_actions_keyboard(
            bot_id,
            refreshed["status"] == "running",
            is_owner(ctx.settings, callback.from_user.id),
        ),
    )


def register_data(ctx: AppContext) -> None:
    router.message.middleware.register(AccessGateMiddleware(ctx))
    router.callback_query.middleware.register(AccessGateMiddleware(ctx))
    router.message.middleware.register(_ContextInjector(ctx))
    router.callback_query.middleware.register(_ContextInjector(ctx))


class _ContextInjector(BaseMiddleware):
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def __call__(self, handler, event, data):
        data["ctx"] = self.ctx
        return await handler(event, data)
