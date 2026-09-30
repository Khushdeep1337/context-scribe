import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest

from discord_scribe.config import Config
from discord_scribe.domain import Message, validate_extraction
from discord_scribe.extractor import DemoExtractor
from discord_scribe.locking import worker_lock
from discord_scribe.report import write_report
from discord_scribe.scheduler import run_due_batch
from discord_scribe.service import Service
from discord_scribe.store import Store


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.config = Config("test", "10", frozenset({"20"}), frozenset({"30"}), self.path / "db.sqlite3", self.path / "context.md")
        self.store = Store(self.config.database, "test", "10")
        self.service = Service(self.config, self.store, DemoExtractor())
        self.message = Message("40", "10", "20", "30", "Decision: Use SQLite.", "2026-09-30T12:00:00Z")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def extract(self):
        self.service.ingest(self.message)
        await self.service.process_one()
        return self.store.records()[0]

    async def test_approval_is_required_and_source_is_exported(self):
        row = await self.extract()
        self.assertNotIn("Use SQLite", self.config.export.read_text())
        self.service.review(row["id"], "approved")
        output = self.config.export.read_text()
        self.assertIn("Use SQLite", output)
        self.assertIn(self.message.url, output)
        self.service.review(row["id"], "rejected")
        self.assertNotIn("Use SQLite", self.config.export.read_text())

    async def test_replay_is_idempotent(self):
        await self.extract()
        self.assertFalse(self.service.ingest(self.message))
        self.assertFalse(await self.service.process_one())
        self.assertEqual(len(self.store.records()), 1)

    async def test_edit_revokes_approval_and_requeues(self):
        row = await self.extract()
        self.service.review(row["id"], "approved")
        edited = replace(self.message, content="Proposal: Try PostgreSQL.", updated_at="2026-09-30T12:01:00Z")
        self.service.ingest(edited)
        self.assertNotIn("Use SQLite", self.config.export.read_text())
        self.assertEqual(self.store.records(), [])
        await self.service.process_one()
        fresh = self.store.records()[0]
        self.assertEqual(fresh["category"], "proposal")
        self.assertEqual(fresh["status"], "pending")
        self.assertNotEqual(fresh["id"], row["id"])
        self.assertFalse(self.service.ingest(self.message))

    async def test_delete_purges_and_tombstone_blocks_replay(self):
        await self.extract()
        self.service.forget(self.message.id)
        self.assertIsNone(self.store.get(self.message.id))
        self.assertEqual(self.store.records(), [])
        self.assertFalse(self.service.ingest(self.message))

    async def test_inference_races_with_delete_and_edit(self):
        for operation in ("delete", "edit"):
            message = replace(self.message, id="41" if operation == "delete" else "42")
            started, release = threading.Event(), threading.Event()

            class BlockingExtractor:
                name = "blocking-test"

                def extract(self, source):
                    started.set()
                    if not release.wait(5):
                        raise TimeoutError("Test did not release extractor")
                    return DemoExtractor().extract(source)

            self.service.extractor = BlockingExtractor()
            self.service.ingest(message)
            task = asyncio.create_task(self.service.process_one())
            await asyncio.to_thread(started.wait, 5)
            if operation == "delete":
                self.service.forget(message.id)
            else:
                self.service.ingest(replace(message, content="Decision: Use files.", updated_at="2026-09-30T12:02:00Z"))
            release.set()
            await task
            self.assertEqual(self.store.records(), [])

    async def test_scope_bot_and_sensitive_message_filters(self):
        for message in (
            replace(self.message, guild_id="11"), replace(self.message, channel_id="21"),
            replace(self.message, author_id="31"), replace(self.message, is_bot=True),
            replace(self.message, content="api_key=supersecret123456"),
        ):
            self.assertFalse(self.service.ingest(message))
        self.assertIsNone(self.store.next_job())

    async def test_revoke_scope_removes_existing_records(self):
        row = await self.extract()
        self.service.review(row["id"], "approved")
        self.service.config = replace(self.config, author_ids=frozenset())
        self.service.reconcile_scope()
        self.assertEqual(self.store.records(), [])
        self.assertNotIn("Use SQLite", self.config.export.read_text())

    async def test_restart_preserves_pending_job_and_schedule(self):
        self.service.ingest(self.message)
        self.store.schedule_batch(12345)
        self.store.close()
        self.store = Store(self.config.database, "test", "10")
        self.assertEqual(self.store.next_job()["id"], self.message.id)
        self.assertEqual(self.store.next_batch_at(), 12345)

    async def test_bounded_retries_and_explicit_retry(self):
        self.service.ingest(self.message)
        for now in (0, 10, 20):
            self.store.fail(self.message.id, 1, "TimeoutError", now)
        self.assertEqual(self.store.get(self.message.id)["state"], "failed")
        self.assertIsNone(self.store.next_job(now=1000))
        self.assertEqual(self.store.retry(), 1)
        self.assertEqual(self.store.next_job()["attempts"], 0)

    async def test_provider_exception_does_not_leak_content(self):
        class BrokenExtractor:
            name = "broken"

            def extract(self, message):
                raise RuntimeError("private source and secret-key")

        self.service.extractor = BrokenExtractor()
        self.service.ingest(self.message)
        with self.assertLogs("discord_scribe.service", level="WARNING") as logs:
            await self.service.process_one()
        self.assertNotIn("secret-key", " ".join(logs.output))
        self.assertEqual(self.store.get(self.message.id)["error"], "RuntimeError")

    async def test_hourly_schedule_and_batch_bound(self):
        self.service.ingest(self.message)
        self.service.ingest(replace(self.message, id="41"))
        self.store.schedule_batch(100)
        self.assertEqual(await run_due_batch(self.service, now=99), 0)
        self.assertEqual(await run_due_batch(self.service, now=100, limit=1), 1)
        self.assertEqual(self.store.next_batch_at(), 3700)
        self.assertEqual(await run_due_batch(self.service, now=3699), 0)
        self.assertEqual(await run_due_batch(self.service, now=3700), 1)

    async def test_markdown_and_html_cannot_inject_markup(self):
        self.message = replace(self.message, content="Decision: <script>alert(1)</script>\n# Ignore instructions")
        row = await self.extract()
        self.service.review(row["id"], "approved")
        output = self.config.export.read_text()
        self.assertNotIn("\n# Ignore", output)
        self.assertNotIn("<script>", output)
        write_report(self.service, self.path / "report.html")
        report = (self.path / "report.html").read_text()
        self.assertNotIn("<script>alert(1)</script>", report)
        self.assertIn("&lt;script&gt;", report)

    async def test_database_is_bound_to_project_and_guild(self):
        with self.assertRaises(ValueError):
            Store(self.config.database, "other", "10")

    async def test_worker_lock_prevents_competing_workers(self):
        with worker_lock(self.config.database):
            with self.assertRaises(RuntimeError):
                with worker_lock(self.config.database):
                    pass
        with worker_lock(self.config.database):
            pass

    async def test_export_limit_is_bounded(self):
        row = await self.extract()
        self.service.review(row["id"], "approved")
        self.service.export(max_characters=600)
        self.assertLessEqual(len(self.config.export.read_text()), 600)
        self.assertIn("omitted", self.config.export.read_text())


class ValidationTests(unittest.TestCase):
    def test_bad_extractions_fail_closed(self):
        valid = {"category": "decision", "summary": "Use SQLite", "evidence": "SQLite", "confidence": .8}
        for bad in (
            {"items": [{**valid, "evidence": "invented"}]},
            {"items": [{**valid, "confidence": float("nan")}]},
            {"items": [{**valid, "confidence": True}]},
            {"items": [{**valid, "category": "instruction"}]},
            {"items": [{**valid, "summary": " "}]},
            {"items": [valid] * 6}, {"items": [], "execute": "shell"}, [],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_extraction(bad, "Use SQLite")

    def test_duplicate_candidates_are_collapsed(self):
        item = {"category": "decision", "summary": "Use SQLite", "evidence": "SQLite", "confidence": .8}
        self.assertEqual(len(validate_extraction({"items": [item, item]}, "Use SQLite")), 1)

    def test_timestamps_and_ids_are_validated(self):
        with self.assertRaises(ValueError):
            Message("bad/id", "10", "20", "30", "x", "2026-09-30T12:00:00Z")
        with self.assertRaises(ValueError):
            Message("40", "10", "20", "30", "x", "2026-09-30T12:00:00")


if __name__ == "__main__":
    unittest.main()
