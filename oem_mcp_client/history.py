from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import safe_endpoint
from .safety import redact


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def user_fingerprint(username: str) -> str:
    return hashlib.sha256(username.strip().lower().encode("utf-8")).hexdigest()[:12]


class HistoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_utc TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    user_fingerprint TEXT NOT NULL,
                    protocol_version TEXT NOT NULL,
                    event TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms INTEGER,
                    tool_count INTEGER,
                    message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_utc TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms INTEGER,
                    arguments_json TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def record_connection(
        self,
        *,
        endpoint: str,
        username: str,
        protocol_version: str,
        event: str,
        status: str,
        latency_ms: int | None = None,
        tool_count: int | None = None,
        message: str = "",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO connections
                (timestamp_utc, endpoint, user_fingerprint, protocol_version, event, status, latency_ms, tool_count, message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    utc_now(),
                    safe_endpoint(endpoint),
                    user_fingerprint(username),
                    protocol_version,
                    event[:40],
                    status[:20],
                    latency_ms,
                    tool_count,
                    message[:500],
                ),
            )

    def record_execution(
        self,
        *,
        endpoint: str,
        tool_name: str,
        status: str,
        arguments: dict[str, Any],
        latency_ms: int | None = None,
        message: str = "",
    ) -> None:
        safe_arguments = json.dumps(redact(arguments), sort_keys=True, default=str)[:4000]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO executions
                (timestamp_utc, endpoint, tool_name, status, latency_ms, arguments_json, message)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (utc_now(), safe_endpoint(endpoint), tool_name[:200], status[:20], latency_ms, safe_arguments, message[:500]),
            )

    def recent_connections(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM connections ORDER BY id DESC LIMIT ?", (max(1, min(limit, 2000)),)).fetchall()
        return [dict(row) for row in rows]

    def recent_executions(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM executions ORDER BY id DESC LIMIT ?", (max(1, min(limit, 2000)),)).fetchall()
        return [dict(row) for row in rows]
