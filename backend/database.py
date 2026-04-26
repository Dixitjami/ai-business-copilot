from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any


class MemoryStore:
    """SQLite-backed memory for chats, appointments, and small business data."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS appointments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    date TEXT NOT NULL,
                    time TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    contact TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    price TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            self._seed_products(conn)

    def _seed_products(self, conn: sqlite3.Connection) -> None:
        products = [
            (
                "Starter Support Plan",
                "Email support, onboarding checklist, and monthly usage summary for small teams.",
                "$49/month",
                {"tier": "starter", "best_for": "small teams"},
            ),
            (
                "Growth Automation Plan",
                "Workflow automation, lead routing, document Q&A, and priority chat support.",
                "$149/month",
                {"tier": "growth", "best_for": "scaling teams"},
            ),
            (
                "Enterprise Knowledge Hub",
                "Custom RAG connectors, private deployment options, audit logs, and SLA support.",
                "Custom pricing",
                {"tier": "enterprise", "best_for": "regulated teams"},
            ),
        ]
        conn.executemany(
            """
            INSERT OR IGNORE INTO products (name, description, price, metadata)
            VALUES (?, ?, ?, ?)
            """,
            [(name, desc, price, json.dumps(meta)) for name, desc, price, meta in products],
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def add_message(
        self,
        user_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO chat_messages (user_id, role, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, role, content, json.dumps(metadata or {}), self._now()),
            )
            return int(cursor.lastrowid)

    def get_history(self, user_id: str, limit: int = 12) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, metadata, created_at
                FROM chat_messages
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        history = []
        for row in reversed(rows):
            history.append(
                {
                    "role": row["role"],
                    "content": row["content"],
                    "metadata": json.loads(row["metadata"] or "{}"),
                    "created_at": row["created_at"],
                }
            )
        return history

    def count_messages(self, user_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM chat_messages WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return int(row["count"])

    def save_appointment(
        self,
        user_id: str,
        customer_name: str,
        date: str,
        time: str,
        topic: str,
        contact: str | None = None,
    ) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO appointments
                    (user_id, customer_name, date, time, topic, contact, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, customer_name, date, time, topic, contact, self._now()),
            )
            appointment_id = int(cursor.lastrowid)

        return {
            "appointment_id": appointment_id,
            "customer_name": customer_name,
            "date": date,
            "time": time,
            "topic": topic,
            "contact": contact,
            "status": "booked",
        }

    def list_appointments(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, customer_name, date, time, topic, contact, created_at
                FROM appointments
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    def search_products(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        pattern = f"%{query.strip()}%"
        sql = """
            SELECT name, description, price, metadata
            FROM products
            WHERE ? = '%%' OR name LIKE ? OR description LIKE ?
            ORDER BY name
            LIMIT ?
        """

        with self._connect() as conn:
            rows = conn.execute(sql, (pattern, pattern, pattern, limit)).fetchall()

        return [
            {
                "name": row["name"],
                "description": row["description"],
                "price": row["price"],
                "metadata": json.loads(row["metadata"] or "{}"),
            }
            for row in rows
        ]
