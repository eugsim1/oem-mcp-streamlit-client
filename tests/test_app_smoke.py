from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_app_renders_all_primary_tabs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OEM_MCP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OEM_MCP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("OEM_MCP_PROFILE_FILE", str(tmp_path / "data" / "profiles.json"))
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(str(app_path)).run(timeout=30)
    assert not app.exception
    labels = [tab.label for tab in app.tabs]
    for required in (
        "Connection",
        "Capabilities",
        "Request",
        "Metrics",
        "Operations",
        "Workspace",
        "Topology",
        "SQL",
        "Incidents",
        "Assistant",
        "Governance",
        "Automation",
        "Usage & cost",
        "History & logs",
        "Diagnostics",
    ):
        assert required in labels
    assert "Host ↔ database associations" in labels
