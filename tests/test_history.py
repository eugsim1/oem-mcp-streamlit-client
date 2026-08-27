from pathlib import Path

from oem_mcp_client.history import HistoryStore


def test_history_redacts_arguments_and_fingerprints_user(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    store = HistoryStore(path)
    store.record_connection(
        endpoint="https://user:password@oem.example/em/api/mcp?x=1",
        username="AdminUser",
        protocol_version="2025-11-25",
        event="connect",
        status="success",
    )
    store.record_execution(
        endpoint="https://oem.example/em/api/mcp",
        tool_name="GetTarget",
        status="success",
        arguments={"password": "do-not-store", "target": "db1"},
    )
    raw = path.read_bytes()
    assert b"do-not-store" not in raw
    assert b"AdminUser" not in raw
    assert "***REDACTED***" in store.recent_executions()[0]["arguments_json"]
    assert store.recent_connections()[0]["endpoint"] == "https://oem.example/em/api/mcp"
