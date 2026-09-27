"""
SQLite persistence for Nightlore.
Two tables: stories (Agent 1 output) and narrations (Agent 2 output),
linked by story_id. This is the app's short-term / session memory --
lets the user revisit past generations via the Chat History panel.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nightlore.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    theme TEXT NOT NULL,
    language TEXT NOT NULL,
    tone TEXT,
    story_text TEXT NOT NULL,
    tokens_used INTEGER,
    was_refused INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions (id)
);

CREATE TABLE IF NOT EXISTS narrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER NOT NULL,
    audio_path TEXT,
    voice TEXT,
    duration_seconds REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (story_id) REFERENCES stories (id)
);
"""


@contextmanager
def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)


def start_session(session_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id) VALUES (?)", (session_id,)
        )


def end_session(session_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )


def save_story(
    session_id: str,
    theme: str,
    language: str,
    tone: str,
    story_text: str,
    tokens_used: int = 0,
    was_refused: bool = False,
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO stories
               (session_id, theme, language, tone, story_text, tokens_used, was_refused)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, theme, language, tone, story_text, tokens_used, int(was_refused)),
        )
        return cur.lastrowid


def save_narration(
    story_id: int, audio_path: str, voice: str, duration_seconds: float
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO narrations (story_id, audio_path, voice, duration_seconds)
               VALUES (?, ?, ?, ?)""",
            (story_id, audio_path, voice, duration_seconds),
        )
        return cur.lastrowid


def get_session_history(session_id: str, limit: int = 20) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """SELECT s.*, n.audio_path, n.duration_seconds
               FROM stories s
               LEFT JOIN narrations n ON n.story_id = s.id
               WHERE s.session_id = ?
               ORDER BY s.created_at DESC
               LIMIT ?""",
            (session_id, limit),
        )
        return cur.fetchall()


def delete_session_history(session_id: str) -> None:
    """Used by 'New chat' -- clears this session's rows but keeps the
    session row itself (or callers can start a fresh session_id instead)."""
    with get_connection() as conn:
        conn.execute(
            """DELETE FROM narrations WHERE story_id IN
               (SELECT id FROM stories WHERE session_id = ?)""",
            (session_id,),
        )
        conn.execute("DELETE FROM stories WHERE session_id = ?", (session_id,))
