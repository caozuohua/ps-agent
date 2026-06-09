import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import uuid
from datetime import datetime, timezone

from task_queue import TaskQueue, ShellTask
from tools.shell import ShellTool
from security.blacklist import CommandBlacklist


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Memory:
    def __init__(self, sqlite_path: str):
        self.sqlite_path = sqlite_path
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(sqlite_path)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self):
        cur = self.conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            open_id TEXT,
            chat_id TEXT,
            direction TEXT,
            content TEXT,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE,
            task_id TEXT,
            step_id TEXT,
            open_id TEXT,
            event_type TEXT,
            event_level TEXT,
            content TEXT,
            created_at TEXT
        )
        """)

        self.conn.commit()

    def add_message(
        self,
        *,
        message_id: Optional[str],
        open_id: Optional[str],
        chat_id: Optional[str],
        direction: str,
        content: str,
    ):
        self.conn.execute(
            """
            INSERT INTO messages (
                message_id, open_id, chat_id, direction, content, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                open_id,
                chat_id,
                direction,
                content,
                now_iso(),
            ),
        )
        self.conn.commit()

    def audit(
        self,
        *,
        event_type: str,
        event_level: str = "info",
        content: Optional[dict[str, Any]] = None,
        open_id: Optional[str] = None,
        task_id: Optional[str] = None,
        step_id: Optional[str] = None,
    ):
        event_id = str(uuid.uuid4())

        self.conn.execute(
            """
            INSERT INTO audit_logs (
                event_id, task_id, step_id, open_id,
                event_type, event_level, content, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                task_id,
                step_id,
                open_id,
                event_type,
                event_level,
                json.dumps(content or {}, ensure_ascii=False),
                now_iso(),
            ),
        )
        self.conn.commit()
