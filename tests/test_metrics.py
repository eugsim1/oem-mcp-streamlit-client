from oem_mcp_client.metrics import candidate_tools, collect_local_metrics


def test_local_metrics_have_required_sections() -> None:
    metrics = collect_local_metrics()
    assert {"host", "memory", "disk", "network", "process"}.issubset(metrics)
    assert 0 <= metrics["memory"]["percent"] <= 100


def test_database_tool_ranking_includes_execute_sql() -> None:
    tools = [
        {"name": "GetHostMetrics", "description": "Read Linux CPU metrics"},
        {"name": "ExecuteSql", "description": "Execute SQL against an authorized database"},
        {"name": "DeleteTarget", "description": "Delete a target"},
    ]
    assert candidate_tools(tools, "host")[0]["name"] == "GetHostMetrics"
    assert candidate_tools(tools, "database")[0]["name"] == "ExecuteSql"
