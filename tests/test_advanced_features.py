from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from oem_mcp_client.assistant import estimated_cost, local_plan
from oem_mcp_client.jobs import BackgroundJobManager
from oem_mcp_client.operations import health_score, infer_topology, topology_dot
from oem_mcp_client.policy import PolicyEngine
from oem_mcp_client.safety import ToolSafetyError, bounded_read_only_sql
from oem_mcp_client.workspace import WorkspaceStore, request_hash


def test_policy_is_deny_first_and_allows_safe_sql() -> None:
    policy = PolicyEngine(
        [
            {"name": "allow", "roles": ["operator"], "tools": ["Get*"], "targets": ["*"], "effect": "allow"},
            {"name": "deny", "roles": ["*"], "tools": ["GetSecret*"], "targets": ["*"], "effect": "deny"},
        ]
    )
    assert policy.evaluate("operator", {"name": "GetTarget"}, {}).allowed
    assert not policy.evaluate("operator", {"name": "GetSecretValue"}, {}).allowed
    assert PolicyEngine().evaluate("operator", {"name": "ExecuteSql"}, {"sql": "SELECT 1 FROM dual"}).allowed


def test_bounded_sql_adds_limit_and_preserves_existing_limit() -> None:
    assert bounded_read_only_sql("SELECT * FROM dual", 25).endswith("FETCH FIRST 25 ROWS ONLY")
    assert bounded_read_only_sql("SELECT * FROM dual FETCH FIRST 3 ROWS ONLY", 25).endswith("3 ROWS ONLY")
    with pytest.raises(ToolSafetyError):
        bounded_read_only_sql("DELETE FROM targets", 25)


def test_local_assistant_uses_only_discovered_tools() -> None:
    tools = [
        {"name": "GetHostMetrics", "description": "Retrieve CPU and memory metrics", "inputSchema": {"type": "object"}},
        {"name": "ListIncidents", "description": "List incidents and severity", "inputSchema": {"type": "object"}},
    ]
    plan = local_plan("list critical incidents", tools)
    assert plan.tool_name == "ListIncidents"
    assert 0 <= plan.confidence <= 1
    assert estimated_cost(1000, 500, 1.0, 2.0) == pytest.approx(0.002)


def test_topology_inference_builds_host_database_edge() -> None:
    result = {"structuredContent": {"items": [{"host_name": "host1", "database_name": "db1"}]}}
    topology = infer_topology(result)
    assert {node["id"] for node in topology.nodes} == {"host1", "db1"}
    assert topology.edges == [{"source": "host1", "target": "db1", "label": "hosts"}]
    assert '"host1" -> "db1"' in topology_dot(topology)
    assert health_score(0, 0, 0) == 100


def test_workspace_artifacts_incidents_approvals_jobs_schedules_and_usage(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.sqlite3")
    store.save_artifact("runbook", "triage", {"steps": ["check alert"]}, "test")
    assert store.list_artifacts("runbook")[0]["name"] == "triage"

    incident_id = store.create_incident("DB latency", "high", "Investigate", "alice")
    store.append_incident(incident_id, "alice", "observation", "CPU high", status="investigating")
    incident = store.list_incidents()[0]
    assert incident["status"] == "investigating"
    assert len(json.loads(incident["timeline_json"])) == 2

    args = {"target": "prod-db", "password": "must-not-persist"}
    approval_id = store.create_approval("https://oem.example/em/api/mcp", "AcknowledgeIncident", args, "alice")
    with pytest.raises(ValueError, match="different"):
        store.decide_approval(approval_id, "ALICE", True)
    store.decide_approval(approval_id, "bob", True, "reviewed")
    assert store.has_valid_approval("https://oem.example/em/api/mcp", "AcknowledgeIncident", args)

    job_id = store.create_job("test", "job", args)
    store.update_job(job_id, "running")
    store.update_job(job_id, "success", result={"ok": True})
    assert store.list_jobs()[0]["status"] == "success"

    store.save_schedule("hourly", "active", "GetHostMetrics", {"target": "host1"}, 60)
    assert store.list_schedules(enabled_only=True)[0]["interval_minutes"] == 60
    store.record_alert(None, "high", "test alert")
    assert store.list_alerts()[0]["message"] == "test alert"

    store.record_usage(
        category="assistant",
        operation="plan",
        provider="oci-generative-ai",
        input_tokens=100,
        output_tokens=20,
        estimated_cost=0.01,
        latency_ms=50,
    )
    assert store.usage_summary()["estimated_cost"] == pytest.approx(0.01)
    assert b"must-not-persist" not in (tmp_path / "workspace.sqlite3").read_bytes()
    assert request_hash("https://oem.example/em/api/mcp", "GetTarget", {}) == request_hash(
        "https://oem.example/em/api/mcp", "GetTarget", {}
    )
    assert request_hash("https://oem.example/em/api/mcp", "GetTarget", {"password": "one"}) != request_hash(
        "https://oem.example/em/api/mcp", "GetTarget", {"password": "two"}
    )


def test_background_job_manager_runs_captured_callable(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.sqlite3")
    manager = BackgroundJobManager(store, max_workers=1)
    try:
        job_id = manager.submit("test", "captured", {"password": "not-stored"}, lambda: {"ok": True})
        deadline = time.monotonic() + 3
        while manager.status(job_id) == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        row = next(item for item in store.list_jobs() if int(item["id"]) == job_id)
        assert row["status"] == "success"
        assert json.loads(row["result_json"]) == {"ok": True}
        assert b"not-stored" not in (tmp_path / "workspace.sqlite3").read_bytes()
    finally:
        manager.shutdown()
