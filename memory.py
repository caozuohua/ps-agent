import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Memory:
    def __init__(self, sqlite_path: str):
        self.sqlite_path = sqlite_path
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)

        self.lock = threading.RLock()

        self.conn = sqlite3.connect(
            sqlite_path,
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self):
        with self.lock:
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

            cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT UNIQUE,
                open_id TEXT,
                chat_id TEXT,
                intent TEXT,
                raw_text TEXT,
                command TEXT,
                mode TEXT,
                timeout INTEGER,
                status TEXT,
                requires_confirm INTEGER DEFAULT 0,
                created_at TEXT,
                started_at TEXT,
                finished_at TEXT,
                summary TEXT
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS task_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                step_id TEXT,
                step_type TEXT,
                command TEXT,
                mode TEXT,
                status TEXT,
                attempt INTEGER,
                returncode INTEGER,
                stdout TEXT,
                stderr TEXT,
                error TEXT,
                started_at TEXT,
                finished_at TEXT
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
        with self.lock:
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

        with self.lock:
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

    def create_shell_task(
        self,
        *,
        task_id: str,
        open_id: str,
        chat_id: str,
        raw_text: str,
        command: str,
        mode: str,
        timeout: int,
        status: str,
        requires_confirm: bool,
    ):
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO tasks (
                    task_id, open_id, chat_id, intent, raw_text,
                    command, mode, timeout, status, requires_confirm,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    open_id,
                    chat_id,
                    "shell",
                    raw_text,
                    command,
                    mode,
                    timeout,
                    status,
                    1 if requires_confirm else 0,
                    now_iso(),
                ),
            )
            self.conn.commit()

    def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        with self.lock:
            cur = self.conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return dict(row)

    def update_task_status(
        self,
        *,
        task_id: str,
        status: str,
        summary: Optional[str] = None,
        mark_started: bool = False,
        mark_finished: bool = False,
    ):
        fields = ["status = ?"]
        values: list[Any] = [status]

        if summary is not None:
            fields.append("summary = ?")
            values.append(summary)

        if mark_started:
            fields.append("started_at = ?")
            values.append(now_iso())

        if mark_finished:
            fields.append("finished_at = ?")
            values.append(now_iso())

        values.append(task_id)

        with self.lock:
            self.conn.execute(
                f"UPDATE tasks SET {', '.join(fields)} WHERE task_id = ?",
                values,
            )
            self.conn.commit()

    def add_task_step(
        self,
        *,
        task_id: str,
        step_id: str,
        step_type: str,
        command: str,
        mode: str,
        status: str,
        attempt: int,
        returncode: Optional[int] = None,
        stdout: str = "",
        stderr: str = "",
        error: str = "",
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
    ):
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO task_steps (
                    task_id, step_id, step_type, command, mode,
                    status, attempt, returncode, stdout, stderr, error,
                    started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    step_id,
                    step_type,
                    command,
                    mode,
                    status,
                    attempt,
                    returncode,
                    stdout,
                    stderr,
                    error,
                    started_at or now_iso(),
                    finished_at or now_iso(),
                ),
            )
            self.conn.commit()
