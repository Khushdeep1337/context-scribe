"""Read-only Discord gateway adapter; no commands or send-message permission."""

import asyncio
from contextlib import suppress
import logging

import discord

from .domain import Message
from .service import Service
from .scheduler import run_due_batch

logger = logging.getLogger(__name__)


class ScribeClient(discord.Client):
    def __init__(self, service: Service):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents, max_messages=None)
        self.service = service
        self.worker: asyncio.Task | None = None

    async def setup_hook(self) -> None:
        if self.service.store.next_batch_at() == 0:
            import time

            self.service.store.schedule_batch(time.time() + self.service.config.interval_seconds)
        self.worker = asyncio.create_task(self.process_queue())

    async def on_ready(self) -> None:
        logger.info("Discord connected; configured capture scope is active")

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        # The SDK's default exception logging may include private payloads.
        logger.error("Discord event failed: event=%s; inspect configuration and status", event_method)

    async def process_queue(self) -> None:
        while True:
            try:
                await run_due_batch(self.service)
                self.service.export()
            except Exception as error:
                logger.error("Worker operation failed: error_type=%s", type(error).__name__)
            await asyncio.sleep(1)

    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        self.service.ingest(Message(
            id=str(message.id), guild_id=str(message.guild.id),
            channel_id=str(message.channel.id), author_id=str(message.author.id),
            content=message.content,
            updated_at=(message.edited_at or message.created_at).isoformat(),
            is_bot=message.author.bot or message.webhook_id is not None,
        ))

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        # Content-only updates avoid a fetch race with a later edit or deletion.
        if str(payload.guild_id) != self.service.config.guild_id or "content" not in payload.data:
            return
        old = self.service.store.get(str(payload.message_id))
        if old is None:
            return
        edited_at = payload.data.get("edited_timestamp")
        if not edited_at:
            return
        self.service.ingest(Message(
            id=old["id"], guild_id=old["guild_id"], channel_id=old["channel_id"],
            author_id=old["author_id"], content=payload.data["content"], updated_at=edited_at,
        ))

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if str(payload.guild_id) == self.service.config.guild_id and str(payload.channel_id) in self.service.config.channel_ids:
            self.service.forget(str(payload.message_id))

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if str(payload.guild_id) == self.service.config.guild_id and str(payload.channel_id) in self.service.config.channel_ids:
            for message_id in payload.message_ids:
                self.service.store.forget(str(message_id))
            self.service.export()

    async def close(self) -> None:
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
        await super().close()
