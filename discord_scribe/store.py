"""SQLite queue and context repository. All methods run on the owning thread."""

from dataclasses import asdict
from pathlib import Path
import sqlite3
import time

from .domain import Candidate, Message


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
    author_id TEXT NOT NULL, content TEXT NOT NULL, updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL CHECK(state IN ('pending','done','failed','deleted')),
    attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS queue_ready ON messages(state, available_at);
CREATE TABLE IF NOT EXISTS context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    category TEXT NOT NULL, summary TEXT NOT NULL, evidence TEXT NOT NULL,
    confidence REAL NOT NULL, model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
    reviewed_at REAL
);
CREATE TABLE IF NOT EXISTS tombstones (message_id TEXT PRIMARY KEY);
PRAGMA user_version = 1;
"""


class Store:
    def __init__(self, path: Path, project: str, guild_id: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA secure_delete = ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError(f"Unsupported database schema version: {version}")
        self.db.executescript(SCHEMA)
        for key, value in (("project", project), ("guild_id", guild_id)):
            existing = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            if existing and existing[0] != value:
                self.db.close()
                raise ValueError("This database belongs to another project or guild")
        with self.db:
            for key, value in (("project", project), ("guild_id", guild_id)):
                self.db.execute("INSERT OR IGNORE INTO metadata VALUES (?, ?)", (key, value))

    def close(self) -> None:
        self.db.close()

    def next_batch_at(self) -> float:
        row = self.db.execute("SELECT value FROM metadata WHERE key='next_batch_at'").fetchone()
        return float(row[0]) if row else 0.0

    def schedule_batch(self, next_at: float) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('next_batch_at', ?)", (str(next_at),))

    def get(self, message_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()

    def ingest(self, message: Message) -> bool:
        with self.db:
            if self.db.execute("SELECT 1 FROM tombstones WHERE message_id=?", (message.id,)).fetchone():
                return False
            old = self.get(message.id)
            if old and (message.updated_at <= old["updated_at"]):
                return False
            values = asdict(message)
            values.pop("is_bot")
            if old:
                # An edit revokes approval before inference starts, even if inference fails.
                self.db.execute("DELETE FROM context WHERE message_id=?", (message.id,))
                self.db.execute(
                    "UPDATE messages SET content=?, updated_at=?, revision=revision+1, "
                    "state='pending', attempts=0, available_at=0, error=NULL WHERE id=?",
                    (message.content, message.updated_at, message.id),
                )
            else:
                self.db.execute(
                    "INSERT INTO messages (id,guild_id,channel_id,author_id,content,updated_at,state) "
                    "VALUES (:id,:guild_id,:channel_id,:author_id,:content,:updated_at,'pending')", values,
                )
        return True

    def forget(self, message_id: str) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO tombstones VALUES (?)", (message_id,))
            self.db.execute("DELETE FROM context WHERE message_id=?", (message_id,))
            self.db.execute("DELETE FROM messages WHERE id=?", (message_id,))

    def next_job(self, now: float | None = None) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM messages WHERE state='pending' AND available_at<=? "
            "ORDER BY updated_at, id LIMIT 1", (time.time() if now is None else now,),
        ).fetchone()

    def complete(self, message_id: str, revision: int, items: list[Candidate], model: str) -> bool:
        with self.db:
            current = self.get(message_id)
            if not current or current["revision"] != revision or current["state"] != "pending":
                return False
            for item in items:
                self.db.execute(
                    "INSERT INTO context (message_id,category,summary,evidence,confidence,model) VALUES (?,?,?,?,?,?)",
                    (message_id, item.category, item.summary, item.evidence, item.confidence, model),
                )
            self.db.execute("UPDATE messages SET state='done', error=NULL WHERE id=?", (message_id,))
        return True

    def fail(self, message_id: str, revision: int, error: str, now: float | None = None) -> None:
        with self.db:
            row = self.get(message_id)
            if not row or row["revision"] != revision or row["state"] != "pending":
                return
            attempts = row["attempts"] + 1
            state = "failed" if attempts >= 3 else "pending"
            available = (time.time() if now is None else now) + 2 ** attempts
            self.db.execute(
                "UPDATE messages SET state=?,attempts=?,available_at=?,error=? WHERE id=?",
                (state, attempts, available, error, message_id),
            )

    def retry(self) -> int:
        with self.db:
            return self.db.execute(
                "UPDATE messages SET state='pending',attempts=0,available_at=0,error=NULL WHERE state='failed'"
            ).rowcount

    def review(self, context_id: int, status: str) -> None:
        if status not in {"approved", "rejected"}:
            raise ValueError("Review status must be approved or rejected")
        with self.db:
            count = self.db.execute(
                "UPDATE context SET status=?,reviewed_at=? WHERE id=?", (status, time.time(), context_id)
            ).rowcount
            if not count:
                raise ValueError("Context item no longer exists; it may have been invalidated by an edit")

    def records(self, status: str | None = None) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT c.*,m.guild_id,m.channel_id,m.author_id,m.updated_at,m.content "
            "FROM context c JOIN messages m ON m.id=c.message_id "
            "WHERE (? IS NULL OR c.status=?) ORDER BY m.updated_at DESC,c.id DESC", (status, status)
        ).fetchall()

    def status(self) -> dict:
        return {
            "messages": dict(self.db.execute("SELECT state,count(*) FROM messages GROUP BY state").fetchall()),
            "context": dict(self.db.execute("SELECT status,count(*) FROM context GROUP BY status").fetchall()),
            "failures": [dict(row) for row in self.db.execute(
                "SELECT id,attempts,error FROM messages WHERE error IS NOT NULL ORDER BY updated_at"
            )],
        }
