import pytest

from oem_mcp_client.safety import ToolSafetyError, is_read_only_sql, redact, tool_is_read_only, validate_tool_call


def test_read_only_sql_filter() -> None:
    assert is_read_only_sql("SELECT target_name FROM mgmt$target")
    assert is_read_only_sql("WITH x AS (SELECT 1 n FROM dual) SELECT n FROM x")
    assert not is_read_only_sql("DELETE FROM table_name")
    assert not is_read_only_sql("SELECT 1 FROM dual; DROP TABLE x")


def test_execute_sql_allows_select_and_rejects_mutation() -> None:
    tool = {"name": "ExecuteSql"}
    validate_tool_call(tool, {"sql": "SELECT 1 FROM dual"})
    with pytest.raises(ToolSafetyError, match="SELECT/WITH"):
        validate_tool_call(tool, {"sql": "UPDATE x SET y = 1"})


def test_unclassified_tool_is_blocked_by_default() -> None:
    with pytest.raises(ToolSafetyError, match="not marked"):
        validate_tool_call({"name": "PerformOperation"}, {})


def test_camel_case_tool_names_are_classified() -> None:
    assert tool_is_read_only({"name": "GetHostMetrics"})
    assert not tool_is_read_only({"name": "DeleteTarget"})


def test_secret_keys_are_redacted() -> None:
    assert redact({"password": "do-not-store", "nested": {"api_token": "do-not-store"}, "value": 1}) == {
        "password": "***REDACTED***",
        "nested": {"api_token": "***REDACTED***"},
        "value": 1,
    }
