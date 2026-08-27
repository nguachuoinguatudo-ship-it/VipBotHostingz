from __future__ import annotations

from aiogram import Bot

from .texts import unicode_text


class StyledBot(Bot):
    async def send_message(self, chat_id, text, *args, **kwargs):
        return await super().send_message(chat_id, unicode_text(text), *args, **kwargs)

    async def edit_message_text(self, text, *args, **kwargs):
        return await super().edit_message_text(unicode_text(text), *args, **kwargs)
