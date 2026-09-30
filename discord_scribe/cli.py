"""Operator commands. Run `python -m discord_scribe --help`."""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sys

from .config import Config
from .domain import Message
from .extractor import DemoExtractor, ModelExtractor
from .locking import worker_lock
from .service import Service
from .store import Store


async def drain(service: Service, limit: int) -> int:
    count = 0
    while count < limit and await service.process_one():
        count += 1
    return count


def demo(path: Path) -> None:
    path = path.resolve()
    if path.exists() and any(path.iterdir()):
        raise ValueError("Demo output directory must be empty; choose a new --output path")
    path.mkdir(parents=True, exist_ok=True)
    config = Config("sample-project", "100", frozenset({"200"}), frozenset({"300"}), path / "scribe.sqlite3", path / "discord-context.md")
    store = Store(config.database, config.project, config.guild_id)
    try:
        service = Service(config, store, DemoExtractor())
        samples = [
            "Decision: Store curated context in SQLite and export Markdown for coding agents.",
            "Proposal: We could add semantic search after measuring retrieval quality.",
            "Constraint: The bot may only capture explicitly opted-in channels and authors.",
            "Action: Write regression tests for messages edited while extraction is running.",
            "Anyone up for lunch?",
        ]
        for index, content in enumerate(samples, 1):
            service.ingest(Message(str(index), "100", "200", "300", content, f"2026-09-30T12:00:0{index}+00:00"))
        asyncio.run(drain(service, 100))
        # Only this labelled synthetic demonstration auto-approves fixture records.
        for row in store.records():
            service.review(row["id"], "approved")
        from .report import write_report

        write_report(service, path / "report.html")
        print(json.dumps(store.status(), indent=2))
        print(f"Synthetic demo: {config.export}\nReport: {path / 'report.html'}")
    finally:
        store.close()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="scribe", description="Turn Discord conversations into reviewed project context.")
    root.add_argument("--config", type=Path, default=Path("scribe.toml"))
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="Listen continuously and extract context hourly")
    process = commands.add_parser("process", help="Process queued messages now using the configured model")
    process.add_argument("--limit", type=int, default=100)
    commands.add_parser("status", help="Show queue counts and safe failure codes")
    listing = commands.add_parser("list", help="Inspect candidates and their full source messages")
    listing.add_argument("--status", choices=("pending", "approved", "rejected"))
    for command in ("approve", "reject"):
        review = commands.add_parser(command, help=f"{command.title()} a context item by ID")
        review.add_argument("id", type=int)
    forget = commands.add_parser("forget", help="Erase a message and prevent its recapture")
    forget.add_argument("message_id")
    commands.add_parser("retry", help="Requeue failed extraction attempts")
    commands.add_parser("export", help="Regenerate the configured Markdown snapshot")
    report = commands.add_parser("report", help="Write a local HTML review report")
    report.add_argument("--output", type=Path, default=Path("data/report.html"))
    example = commands.add_parser("demo", help="Run synthetic fixtures without Discord, keys, or an LLM")
    example.add_argument("--output", type=Path, default=Path("demo-output"))
    return root


def execute(args: argparse.Namespace) -> None:
    if args.command == "demo":
        demo(args.output)
        return
    config = Config.load(args.config)
    if args.command == "process" and not 1 <= args.limit <= 1000:
        raise ValueError("--limit must be from 1 to 1000")
    store = Store(config.database, config.project, config.guild_id)
    try:
        extractor = ModelExtractor(config) if args.command in {"run", "process"} else DemoExtractor()
        service = Service(config, store, extractor)
        service.reconcile_scope()
        if args.command == "run":
            token = os.environ.get("DISCORD_TOKEN")
            if not token:
                raise ValueError("Set DISCORD_TOKEN before starting the bot")
            from .discord_adapter import ScribeClient

            with worker_lock(config.database):
                ScribeClient(service).run(token, log_handler=None)
        elif args.command == "process":
            with worker_lock(config.database):
                print(f"Attempted {asyncio.run(drain(service, args.limit))} queued messages")
        elif args.command == "status":
            print(json.dumps({**store.status(), "next_batch_at": store.next_batch_at()}, indent=2))
        elif args.command == "list":
            print(json.dumps([dict(row) for row in store.records(args.status)], indent=2, ensure_ascii=True))
        elif args.command in {"approve", "reject"}:
            service.review(args.id, "approved" if args.command == "approve" else "rejected")
            print(f"Context {args.id}: {args.command}d; Markdown refreshed")
        elif args.command == "forget":
            service.forget(args.message_id)
            print("Message removed from the live database and current export")
        elif args.command == "retry":
            print(f"Requeued {store.retry()} failed messages")
        elif args.command == "report":
            from .report import write_report

            write_report(service, args.output)
            print(args.output.resolve())
        elif args.command == "export":
            service.export()
            print(config.export)
    finally:
        store.close()


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        execute(parser().parse_args())
    except (ValueError, OSError, RuntimeError) as error:
        print(f"scribe: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except ImportError:
        print('scribe: Install integrations with python -m pip install -e ".[all]"', file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
