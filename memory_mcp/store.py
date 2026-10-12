"""SQLite storage for raw memories, composted lessons, deletion receipts and the sandbox outbox."""

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("MEMORY_DB", Path(__file__).resolve().parent.parent / "data" / "memory.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    lesson TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deletion_receipts (
    receipt_id TEXT PRIMARY KEY,
    user_email_digest TEXT NOT NULL,
    deleted_count INTEGER NOT NULL,
    content_digest TEXT NOT NULL,
    lessons_kept INTEGER NOT NULL,
    deleted_at TEXT NOT NULL
);
-- Sandbox sink for export_memory: stands in for "data left the building".
CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    destination TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def add_memory(user_email: str, content: str) -> str:
    memory_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?)",
            (memory_id, user_email.lower(), content, now()),
        )
    return memory_id


def get_memories(user_email: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, content, created_at FROM memories WHERE user_email = ? ORDER BY created_at",
            (user_email.lower(),),
        ).fetchall()
    return [dict(r) for r in rows]


def add_lesson(lesson: str, source_digest: str) -> str:
    lesson_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute("INSERT INTO lessons VALUES (?, ?, ?, ?)", (lesson_id, lesson, source_digest, now()))
    return lesson_id


def get_lessons() -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT lesson FROM lessons ORDER BY created_at").fetchall()
    return [r["lesson"] for r in rows]


def delete_memories(user_email: str, lessons_kept: int) -> dict:
    """Hard-delete a user's raw memories and return a receipt that proves what was removed
    without containing any of it (only SHA-256 digests)."""
    memories = get_memories(user_email)
    content_digest = digest(json.dumps(sorted(m["content"] for m in memories)))
    receipt = {
        "receipt_id": str(uuid.uuid4()),
        "user_email_digest": digest(user_email.lower()),
        "deleted_count": len(memories),
        "content_digest": content_digest,
        "lessons_kept": lessons_kept,
        "deleted_at": now(),
    }
    with connect() as conn:
        conn.execute("DELETE FROM memories WHERE user_email = ?", (user_email.lower(),))
        conn.execute(
            "INSERT INTO deletion_receipts VALUES (:receipt_id, :user_email_digest, :deleted_count,"
            " :content_digest, :lessons_kept, :deleted_at)",
            receipt,
        )
    # Reclaim freed pages so deleted rows don't linger in the SQLite file.
    conn = connect()
    conn.execute("VACUUM")
    conn.close()
    return receipt


def write_outbox(destination: str, payload: str) -> str:
    outbox_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute("INSERT INTO outbox VALUES (?, ?, ?, ?)", (outbox_id, destination, payload, now()))
    return outbox_id


def get_outbox() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT destination, payload, created_at FROM outbox ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def find_anywhere(needle: str) -> list[str]:
    """Return the tables (and the raw DB file) where `needle` still appears."""
    hits = []
    with connect() as conn:
        for table, column in (("memories", "content"), ("lessons", "lesson"), ("outbox", "payload")):
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} LIKE ?", (f"%{needle}%",)
            ).fetchone()[0]
            if count:
                hits.append(table)
    if DB_PATH.exists() and needle.encode("utf-8") in DB_PATH.read_bytes():
        hits.append("db_file_bytes")
    return hits


def reset() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
