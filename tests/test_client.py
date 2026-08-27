from __future__ import annotations

from typing import Any

import pytest

from oem_mcp_client.client import McpProtocolError, OemMcpClient
from oem_mcp_client.config import ConnectionConfig


class FakeResponse:
    def __init__(self, payload: dict[str, Any] | None, *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.reason = "fake"
        self.content = b"" if payload is None else b"{}"

    def json(self) -> dict[str, Any]:
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.auth: Any = None
        self.calls: list[dict[str, Any]] = []

    def post(self, _url: str, *, json: dict[str, Any], headers: dict[str, str], **_kwargs: Any) -> FakeResponse:
        self.calls.append({"json": json, "headers": headers})
        method = json["method"]
        request_id = json.get("id")
        if method == "initialize":
            return FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "serverInfo": {"name": "OEM"},
                        "capabilities": {"tools": {}},
                    },
                },
                headers={"Mcp-Session-Id": "session-123"},
            )
        if method == "notifications/initialized":
            return FakeResponse(None, status=202)
        if method == "tools/list":
            cursor = (json.get("params") or {}).get("cursor")
            page = (
                [{"name": "GetTargets", "inputSchema": {"type": "object"}}]
                if not cursor
                else [{"name": "ExecuteSql", "inputSchema": {"type": "object"}}]
            )
            result = {"tools": page}
            if not cursor:
                result["nextCursor"] = "page-2"
            return FakeResponse({"jsonrpc": "2.0", "id": request_id, "result": result})
        if method in {"prompts/list", "resources/list", "resources/templates/list"}:
            key = {"prompts/list": "prompts", "resources/list": "resources", "resources/templates/list": "resourceTemplates"}[method]
            return FakeResponse({"jsonrpc": "2.0", "id": request_id, "result": {key: []}})
        if method == "tools/call":
            return FakeResponse({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "ok"}]}})
        if method == "ping":
            return FakeResponse({"jsonrpc": "2.0", "id": request_id, "result": {}})
        raise AssertionError(method)

    def close(self) -> None:
        return None


def make_client() -> tuple[OemMcpClient, FakeSession]:
    session = FakeSession()
    client = OemMcpClient(ConnectionConfig("https://oem.example/em/api/mcp", "operator"), "secret", session=session)
    return client, session


def test_initialize_discover_paginate_and_call() -> None:
    client, session = make_client()
    client.initialize()
    discovered = client.discover_all()
    assert client.session_id == "session-123"
    assert [tool["name"] for tool in discovered["tools"]] == ["GetTargets", "ExecuteSql"]
    assert session.calls[1]["json"] == {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert session.calls[1]["headers"]["Mcp-Session-Id"] == "session-123"
    assert client.call_tool("GetTargets", {})["content"][0]["text"] == "ok"


def test_call_before_initialize_is_rejected() -> None:
    client, _ = make_client()
    with pytest.raises(McpProtocolError, match="Initialize"):
        client.call_tool("GetTargets", {})


def test_client_requires_password() -> None:
    with pytest.raises(ValueError, match="password"):
        OemMcpClient(ConnectionConfig("https://oem.example/em/api/mcp", "operator"), "")
