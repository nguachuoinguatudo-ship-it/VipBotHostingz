from __future__ import annotations

import aiohttp
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, Message

from .texts import unicode_text


class StyledBot(Bot):
    async def send_message(self, chat_id, text, *args, **kwargs):
        return await super().send_message(chat_id, unicode_text(text), *args, **kwargs)

    async def edit_message_text(self, text, *args, **kwargs):
        return await super().edit_message_text(unicode_text(text), *args, **kwargs)

    async def send_rich_message(self, chat_id, html, fallback_text, fallback_markup: InlineKeyboardMarkup | None = None) -> Message:
        payload = {"chat_id": chat_id, "rich_message": {"html": html}}
        url = f"https://api.telegram.org/bot{self.token}/sendRichMessage"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=20) as response:
                    result = await response.json()
            if result.get("ok"):
                return Message.model_validate(result["result"])
        except Exception:
            pass
        return await self.send_message(chat_id, fallback_text, reply_markup=fallback_markup)
