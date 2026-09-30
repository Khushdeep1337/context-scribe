"""Offline adapter contracts: no Discord connection or paid model requests."""

from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from discord_scribe.config import Config
from discord_scribe.domain import Message
from discord_scribe.extractor import DemoExtractor, ModelExtractor
from discord_scribe.service import Service
from discord_scribe.store import Store


class ModelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.config = Config("test", "10", frozenset({"20"}), frozenset({"30"}), Path("unused.db"), Path("unused.md"))
        self.message = Message("40", "10", "20", "30", "Use SQLite.", "2026-09-30T12:00:00Z")

    def test_missing_key_fails_before_a_request(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            ModelExtractor(self.config)

    def test_hosted_and_local_arguments(self):
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"items": []}'))])
        fake = SimpleNamespace(completion=lambda **kwargs: response)
        with patch.dict("sys.modules", {"litellm": fake}), patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-key"}):
            with patch.object(fake, "completion", return_value=response) as call:
                self.assertEqual(ModelExtractor(self.config).extract(self.message), {"items": []})
                request = call.call_args.kwargs
                self.assertEqual(request["api_key"], "synthetic-key")
                self.assertEqual(request["num_retries"], 0)
                self.assertNotIn("tools", request)
                self.assertNotIn("synthetic-key", json.dumps(request["messages"]))
                local = replace(self.config, model="ollama_chat/llama3.2", api_base="http://127.0.0.1:11434")
                ModelExtractor(local).extract(self.message)
                self.assertIsNone(call.call_args.kwargs["api_key"])

    def test_truncated_or_non_json_response_is_rejected(self):
        for reason, content in (("length", '{"items": []}'), ("stop", "not JSON")):
            response = SimpleNamespace(choices=[SimpleNamespace(finish_reason=reason, message=SimpleNamespace(content=content))])
            fake = SimpleNamespace(completion=lambda **kwargs: response)
            with patch.dict("sys.modules", {"litellm": fake}), patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-key"}), self.assertRaises(ValueError):
                ModelExtractor(self.config).extract(self.message)


@unittest.skipUnless(importlib.util.find_spec("discord"), "Install the Discord extra for gateway adapter tests")
class DiscordAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_edit_delete_and_dm_scope(self):
        from discord_scribe.discord_adapter import ScribeClient

        with tempfile.TemporaryDirectory() as temp:
            config = Config("test", "10", frozenset({"20"}), frozenset({"30"}), Path(temp) / "db.sqlite3", Path(temp) / "context.md")
            store = Store(config.database, "test", "10")
            service = Service(config, store, DemoExtractor())
            client = ScribeClient(service)
            try:
                await client.on_message(SimpleNamespace(guild=None))
                self.assertIsNone(store.next_job())
                service.ingest(Message("40", "10", "20", "30", "Decision: Use SQLite.", "2026-09-30T12:00:00Z"))
                await client.on_raw_message_edit(SimpleNamespace(
                    guild_id=10, message_id=40,
                    data={"content": "Proposal: Try files.", "edited_timestamp": "2026-09-30T12:01:00Z"},
                ))
                self.assertEqual(store.get("40")["content"], "Proposal: Try files.")
                await client.on_raw_message_delete(SimpleNamespace(guild_id=11, channel_id=20, message_id=40))
                self.assertIsNotNone(store.get("40"))
                await client.on_raw_message_delete(SimpleNamespace(guild_id=10, channel_id=20, message_id=40))
                self.assertIsNone(store.get("40"))
            finally:
                await client.close()
                store.close()


class ConfigTests(unittest.TestCase):
    def test_example_config_paths_and_hourly_default(self):
        config = Config.load(Path(__file__).resolve().parents[1] / "scribe.example.toml")
        self.assertEqual(config.interval_seconds, 3600)
        self.assertTrue(config.database.is_absolute())

    def test_insecure_remote_provider_and_invalid_interval_rejected(self):
        source = (Path(__file__).resolve().parents[1] / "scribe.example.toml").read_text()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            for content in (
                source + '\napi_base = "http://remote.example/v1"\n',
                source.replace("interval_seconds = 3600", "interval_seconds = true"),
            ):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    Config.load(path)
