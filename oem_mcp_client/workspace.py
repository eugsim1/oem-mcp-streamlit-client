from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import safe_endpoint
from .safety import SECRET_KEY_RE, redact


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any, limit: int = 200_000) -> str:
    return json.dumps(redact(value), sort_keys=True, default=str)[:limit]


def _approval_material(value: Any) -> Any:
    """Bind secret-valued fields without retaining their cleartext in approval state."""
    if isinstance(value, dict):
        material: dict[str, Any] = {}
        for key, item in value.items():
            if SECRET_KEY_RE.search(str(key)):
                encoded = json.dumps(item, sort_keys=True, default=str).encode("utf-8")
                material[str(key)] = {"secret_sha256": hashlib.sha256(encoded).hexdigest()}
            else:
                material[str(key)] = _approval_material(item)
        return material
    if isinstance(value, list):
        return [_approval_material(item) for item in value]
    return value


def request_hash(endpoint: str, tool_name: str, arguments: dict[str, Any]) -> str:
    canonical_arguments = json.dumps(_approval_material(arguments), sort_keys=True, separators=(",", ":"), default=str)
    material = f"{safe_endpoint(endpoint)}\n{tool_name}\n{canonical_arguments}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class WorkspaceStore:
    """Persistent, redacted workspace state for operator features."""

    ARTIFACT_KINDS = {"dashboard", "runbook", "sql-query"}

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
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL,
                    UNIQUE(kind, name)
                );
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    timeline_json TEXT NOT NULL DEFAULT '[]',
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_hash TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    requester TEXT NOT NULL,
                    approver TEXT,
                    status TEXT NOT NULL,
                    decision_note TEXT NOT NULL DEFAULT '',
                    created_utc TEXT NOT NULL,
                    expires_utc TEXT NOT NULL,
                    decided_utc TEXT
                );
                CREATE INDEX IF NOT EXISTS approvals_hash_status ON approvals(request_hash, status);
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    created_utc TEXT NOT NULL,
                    started_utc TEXT,
                    finished_utc TEXT
                );
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    profile_name TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    interval_minutes INTEGER NOT NULL,
                    next_run_utc TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_utc TEXT NOT NULL,
                    category TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    estimated_cost REAL NOT NULL DEFAULT 0,
                    latency_ms INTEGER,
                    status TEXT NOT NULL,
                    context TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def save_artifact(self, kind: str, name: str, payload: Any, description: str = "") -> None:
        if kind not in self.ARTIFACT_KINDS:
            raise ValueError(f"Unsupported artifact kind: {kind}")
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Artifact name is required.")
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO artifacts(kind, name, description, payload_json, created_utc, updated_utc)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, name) DO UPDATE SET
                    description=excluded.description,
                    payload_json=excluded.payload_json,
                    updated_utc=excluded.updated_utc""",
                (kind, clean_name[:120], description[:500], _json(payload), now, now),
            )

    def list_artifacts(self, kind: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        query = "SELECT * FROM artifacts"
        params: list[Any] = []
        if kind:
            query += " WHERE kind = ?"
            params.append(kind)
        query += " ORDER BY updated_utc DESC LIMIT ?"
        params.append(max(1, min(limit, 2000)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def create_incident(self, title: str, severity: str, summary: str, actor: str) -> int:
        if not title.strip():
            raise ValueError("Incident title is required.")
        now = utc_now()
        timeline = [{"timestamp_utc": now, "actor": actor[:80], "event": "created", "note": summary[:1000]}]
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO incidents(title, severity, status, summary, timeline_json, created_utc, updated_utc)
                VALUES (?, ?, 'open', ?, ?, ?, ?)""",
                (title[:200], severity[:20], summary[:4000], _json(timeline), now, now),
            )
            return int(cursor.lastrowid)

    def append_incident(self, incident_id: int, actor: str, event: str, note: str, *, status: str | None = None) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT timeline_json, status FROM incidents WHERE id = ?", (incident_id,)).fetchone()
            if row is None:
                raise ValueError("Incident not found.")
            timeline = json.loads(row["timeline_json"] or "[]")
            timeline.append({"timestamp_utc": utc_now(), "actor": actor[:80], "event": event[:80], "note": note[:4000]})
            connection.execute(
                "UPDATE incidents SET timeline_json = ?, status = ?, updated_utc = ? WHERE id = ?",
                (_json(timeline), (status or row["status"])[:20], utc_now(), incident_id),
            )

    def list_incidents(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM incidents ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, updated_utc DESC LIMIT ?",
                (max(1, min(limit, 2000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_approval(
        self,
        endpoint: str,
        tool_name: str,
        arguments: dict[str, Any],
        requester: str,
        ttl_minutes: int = 30,
    ) -> int:
        clean_requester = requester.strip()
        if not clean_requester:
            raise ValueError("Requester identity is required.")
        now = datetime.now(timezone.utc)
        digest = request_hash(endpoint, tool_name, arguments)
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO approvals
                (request_hash, endpoint, tool_name, arguments_json, requester, status, created_utc, expires_utc)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (
                    digest,
                    safe_endpoint(endpoint),
                    tool_name[:200],
                    _json(arguments),
                    clean_requester[:80],
                    now.isoformat(timespec="seconds"),
                    (now + timedelta(minutes=max(5, min(ttl_minutes, 1440)))).isoformat(timespec="seconds"),
                ),
            )
            return int(cursor.lastrowid)

    def decide_approval(self, approval_id: int, approver: str, approve: bool, note: str = "") -> None:
        clean_approver = approver.strip()
        if not clean_approver:
            raise ValueError("Approver identity is required.")
        with self._connect() as connection:
            row = connection.execute("SELECT requester, status, expires_utc FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if row is None:
                raise ValueError("Approval request not found.")
            if row["status"] != "pending":
                raise ValueError("Approval request has already been decided.")
            if row["requester"].casefold() == clean_approver.casefold():
                raise ValueError("Requester and approver must be different operators.")
            if datetime.fromisoformat(row["expires_utc"]) <= datetime.now(timezone.utc):
                status = "expired"
            else:
                status = "approved" if approve else "rejected"
            connection.execute(
                "UPDATE approvals SET status = ?, approver = ?, decision_note = ?, decided_utc = ? WHERE id = ?",
                (status, clean_approver[:80], note[:1000], utc_now(), approval_id),
            )

    def has_valid_approval(self, endpoint: str, tool_name: str, arguments: dict[str, Any]) -> bool:
        digest = request_hash(endpoint, tool_name, arguments)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id FROM approvals
                WHERE request_hash = ? AND status = 'approved' AND expires_utc > ?
                ORDER BY id DESC LIMIT 1""",
                (digest, utc_now()),
            ).fetchone()
        return row is not None

    def list_approvals(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM approvals ORDER BY id DESC LIMIT ?", (max(1, min(limit, 2000)),)).fetchall()
        return [dict(row) for row in rows]

    def create_job(self, kind: str, name: str, payload: Any) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO jobs(kind, name, status, payload_json, created_utc)
                VALUES (?, ?, 'queued', ?, ?)""",
                (kind[:40], name[:200], _json(payload), utc_now()),
            )
            return int(cursor.lastrowid)

    def update_job(self, job_id: int, status: str, *, result: Any = None, error: str = "") -> None:
        now = utc_now()
        if status == "running":
            with self._connect() as connection:
                connection.execute(
                    "UPDATE jobs SET status = ?, error = ?, started_utc = ? WHERE id = ?",
                    (status[:20], error[:1000], now, job_id),
                )
            return
        if status in {"success", "failed", "cancelled"}:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE jobs SET status = ?, error = ?, finished_utc = ?, result_json = ? WHERE id = ?",
                    (status[:20], error[:1000], now, _json(result) if result is not None else None, job_id),
                )
            return
        with self._connect() as connection:
            connection.execute("UPDATE jobs SET status = ?, error = ? WHERE id = ?", (status[:20], error[:1000], job_id))

    def list_jobs(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 2000)),)).fetchall()
        return [dict(row) for row in rows]

    def save_schedule(
        self,
        name: str,
        profile_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        interval_minutes: int,
    ) -> None:
        interval = max(5, min(int(interval_minutes), 10080))
        now = datetime.now(timezone.utc)
        next_run = now + timedelta(minutes=interval)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO schedules
                (name, profile_name, tool_name, arguments_json, interval_minutes, next_run_utc, enabled, created_utc, updated_utc)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    profile_name=excluded.profile_name,
                    tool_name=excluded.tool_name,
                    arguments_json=excluded.arguments_json,
                    interval_minutes=excluded.interval_minutes,
                    next_run_utc=excluded.next_run_utc,
                    enabled=1,
                    updated_utc=excluded.updated_utc""",
                (
                    name[:120],
                    profile_name[:120],
                    tool_name[:200],
                    _json(arguments),
                    interval,
                    next_run.isoformat(timespec="seconds"),
                    utc_now(),
                    utc_now(),
                ),
            )

    def list_schedules(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM schedules"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY next_run_utc"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [dict(row) for row in rows]

    def mark_schedule_run(self, schedule_id: int, interval_minutes: int) -> None:
        next_run = datetime.now(timezone.utc) + timedelta(minutes=max(5, interval_minutes))
        with self._connect() as connection:
            connection.execute(
                "UPDATE schedules SET next_run_utc = ?, updated_utc = ? WHERE id = ?",
                (next_run.isoformat(timespec="seconds"), utc_now(), schedule_id),
            )

    def record_alert(self, schedule_id: int | None, severity: str, message: str, status: str = "open") -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO alerts(schedule_id, severity, message, status, created_utc) VALUES (?, ?, ?, ?, ?)",
                (schedule_id, severity[:20], message[:2000], status[:20], utc_now()),
            )

    def list_alerts(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (max(1, min(limit, 2000)),)).fetchall()
        return [dict(row) for row in rows]

    def record_usage(
        self,
        *,
        category: str,
        operation: str,
        provider: str,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost: float = 0,
        latency_ms: int | None = None,
        status: str = "success",
        context: str = "",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO usage_events
                (timestamp_utc, category, operation, provider, model, input_tokens, output_tokens,
                 estimated_cost, latency_ms, status, context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    utc_now(),
                    category[:40],
                    operation[:200],
                    provider[:80],
                    model[:200],
                    max(0, int(input_tokens)),
                    max(0, int(output_tokens)),
                    max(0.0, float(estimated_cost)),
                    latency_ms,
                    status[:20],
                    context[:200],
                ),
            )

    def list_usage(self, limit: int = 2000) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM usage_events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 10000)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def usage_summary(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS events, COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(estimated_cost), 0) AS estimated_cost,
                COALESCE(AVG(latency_ms), 0) AS average_latency_ms
                FROM usage_events"""
            ).fetchone()
        return dict(row) if row else {}
