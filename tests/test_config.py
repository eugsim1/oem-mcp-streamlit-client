from pathlib import Path

import pytest

from oem_mcp_client.config import ConfigurationError, ConnectionConfig, ProfileStore, normalized_endpoint, safe_endpoint


def test_endpoint_is_normalized_and_query_removed() -> None:
    assert normalized_endpoint("https://oem.example:7803/em/api/mcp/?ignored=1") == "https://oem.example:7803/em/api/mcp"
    assert safe_endpoint("https://user:password@oem.example:7803/em/api/mcp?x=1") == "https://oem.example:7803/em/api/mcp"


def test_endpoint_rejects_embedded_credentials() -> None:
    with pytest.raises(ConfigurationError, match="credentials"):
        normalized_endpoint("https://user:password@oem.example/em/api/mcp")


def test_http_requires_explicit_override() -> None:
    config = ConnectionConfig("http://oem.example/em/api/mcp", "operator")
    with pytest.raises(ConfigurationError, match="HTTP is disabled"):
        config.validated()
    assert config.validated(allow_http=True).endpoint.startswith("http://")


def test_profile_store_contains_no_password(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profiles.json")
    store.save("prod", ConnectionConfig("https://oem.example/em/api/mcp", "operator"))
    raw = (tmp_path / "profiles.json").read_text(encoding="utf-8")
    assert "password" not in raw.lower()
    assert store.load()["prod"]["username"] == "operator"
