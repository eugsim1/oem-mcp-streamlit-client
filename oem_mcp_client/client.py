from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from .config import SUPPORTED_PROTOCOLS, ConnectionConfig, safe_endpoint

LOGGER = logging.getLogger("oem_mcp_client.transport")


class McpClientError(RuntimeError):
    """Base exception for transport and protocol failures."""


class McpHttpError(McpClientError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"OEM MCP HTTP {status_code}: {message}")
        self.status_code = status_code


class McpProtocolError(McpClientError):
    pass


class McpSessionExpired(McpHttpError):
    pass


@dataclass(frozen=True)
class RpcTiming:
    method: str
    elapsed_ms: int
    status_code: int


class OemMcpClient:
    """Minimal synchronous MCP client for Oracle Enterprise Manager's HTTP endpoint."""

    def __init__(
        self,
        config: ConnectionConfig,
        password: str,
        *,
        session: requests.Session | None = None,
        allow_http: bool = False,
    ) -> None:
        self.config = config.validated(allow_http=allow_http)
        if not password:
            raise ValueError("The Enterprise Manager password is required.")
        self._session = session or requests.Session()
        self._session.auth = HTTPBasicAuth(self.config.username, password)
        self._ids = itertools.count(1)
        self._rpc_lock = RLock()
        self.session_id: str | None = None
        self.negotiated_protocol = self.config.protocol_version
        self.server_info: dict[str, Any] = {}
        self.server_capabilities: dict[str, Any] = {}
        self.instructions = ""
        self.initialized = False
        self.last_timing: RpcTiming | None = None

    def _headers(self, method: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self.negotiated_protocol,
            "User-Agent": "oem-mcp-streamlit-client/1.0.0",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    @staticmethod
    def _response_error(response: requests.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict) and error.get("message"):
                    return str(error["message"])[:300]
        except (ValueError, TypeError):
            pass
        return response.reason or "request failed"

    def _post(self, payload: dict[str, Any], *, allow_empty: bool = False) -> dict[str, Any] | None:
        method = str(payload.get("method", "unknown"))
        start = time.monotonic()
        try:
            response = self._session.post(
                self.config.endpoint,
                json=payload,
                headers=self._headers(method),
                timeout=self.config.timeout_seconds,
                verify=self.config.requests_verify,
            )
        except requests.RequestException as exc:
            LOGGER.warning(
                "MCP transport failure method=%s endpoint=%s type=%s", method, safe_endpoint(self.config.endpoint), type(exc).__name__
            )
            raise McpClientError(f"Could not reach the OEM MCP endpoint ({type(exc).__name__}).") from exc

        elapsed_ms = round((time.monotonic() - start) * 1000)
        self.last_timing = RpcTiming(method=method, elapsed_ms=elapsed_ms, status_code=response.status_code)
        LOGGER.info("MCP response method=%s status=%s elapsed_ms=%s", method, response.status_code, elapsed_ms)
        if response.status_code == 404 and self.session_id:
            raise McpSessionExpired(404, "The MCP session expired; reconnect before retrying.")
        if response.status_code >= 400:
            raise McpHttpError(response.status_code, self._response_error(response))
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = session_id
        if response.status_code in {202, 204} or not response.content:
            if allow_empty:
                return None
            raise McpProtocolError(f"{method} returned an empty response.")
        try:
            data = response.json()
        except ValueError as exc:
            raise McpProtocolError(f"{method} did not return JSON.") from exc
        if not isinstance(data, dict) or data.get("jsonrpc") != "2.0":
            raise McpProtocolError(f"{method} returned an invalid JSON-RPC response.")
        return data

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._rpc_lock:
            payload: dict[str, Any] = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
            if params is not None:
                payload["params"] = params
            response = self._post(payload)
            assert response is not None
            if response.get("id") != payload["id"]:
                raise McpProtocolError(f"{method} returned a mismatched JSON-RPC id.")
            if "error" in response:
                error = response.get("error") or {}
                code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
                message = error.get("message", "MCP request failed") if isinstance(error, dict) else "MCP request failed"
                raise McpProtocolError(f"{method} failed ({code}): {message}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise McpProtocolError(f"{method} returned no result object.")
            return result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        with self._rpc_lock:
            payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                payload["params"] = params
            self._post(payload, allow_empty=True)

    def initialize(self) -> dict[str, Any]:
        result = self.rpc(
            "initialize",
            {
                "protocolVersion": self.config.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "oem-mcp-streamlit-client", "version": "1.0.0"},
            },
        )
        negotiated = str(result.get("protocolVersion", ""))
        if negotiated not in SUPPORTED_PROTOCOLS:
            raise McpProtocolError(f"Server selected unsupported protocol version: {negotiated or 'missing'}")
        self.negotiated_protocol = negotiated
        self.server_info = dict(result.get("serverInfo") or {})
        self.server_capabilities = dict(result.get("capabilities") or {})
        self.instructions = str(result.get("instructions") or "")
        self.notify("notifications/initialized")
        self.initialized = True
        return result

    def _require_initialized(self) -> None:
        if not self.initialized:
            raise McpProtocolError("Initialize the MCP session before using it.")

    def list_paginated(self, method: str, result_key: str) -> list[dict[str, Any]]:
        self._require_initialized()
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(100):
            params = {"cursor": cursor} if cursor else {}
            result = self.rpc(method, params)
            page = result.get(result_key, [])
            if not isinstance(page, list):
                raise McpProtocolError(f"{method} returned an invalid {result_key} list.")
            items.extend(item for item in page if isinstance(item, dict))
            cursor_value = result.get("nextCursor")
            cursor = str(cursor_value) if cursor_value else None
            if not cursor:
                return items
        raise McpProtocolError(f"{method} exceeded the 100-page safety limit.")

    def discover_all(self) -> dict[str, Any]:
        tools = self.list_paginated("tools/list", "tools")
        result: dict[str, Any] = {
            "tools": tools,
            "prompts": [],
            "resources": [],
            "resourceTemplates": [],
            "errors": {},
            "serverInfo": self.server_info,
            "serverCapabilities": self.server_capabilities,
            "instructions": self.instructions,
            "protocolVersion": self.negotiated_protocol,
        }
        for method, key in (
            ("prompts/list", "prompts"),
            ("resources/list", "resources"),
            ("resources/templates/list", "resourceTemplates"),
        ):
            try:
                result[key] = self.list_paginated(method, key)
            except McpClientError as exc:
                result["errors"][method] = str(exc)
        return result

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_initialized()
        if not name.strip():
            raise ValueError("Tool name is required.")
        return self.rpc("tools/call", {"name": name, "arguments": arguments or {}})

    def ping(self) -> dict[str, Any]:
        self._require_initialized()
        return self.rpc("ping", {})

    def close(self, terminate_remote: bool = False) -> None:
        if terminate_remote and self.session_id:
            try:
                self._session.delete(
                    self.config.endpoint,
                    headers=self._headers("session/delete"),
                    timeout=min(self.config.timeout_seconds, 15),
                    verify=self.config.requests_verify,
                )
            except requests.RequestException:
                LOGGER.info("Remote MCP session deletion was not available.")
        self._session.close()
        self.initialized = False

    def __enter__(self) -> OemMcpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
