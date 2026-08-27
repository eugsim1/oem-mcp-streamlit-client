from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

import pandas as pd
import streamlit as st

from oem_mcp_client.advanced_ui import (
    render_assistant,
    render_automation,
    render_governance,
    render_incidents,
    render_operations,
    render_sql,
    render_topology,
    render_usage,
    render_workspace,
)
from oem_mcp_client.client import McpClientError, OemMcpClient
from oem_mcp_client.config import SUPPORTED_PROTOCOLS, ConnectionConfig, ProfileStore, runtime_paths, safe_endpoint
from oem_mcp_client.history import HistoryStore
from oem_mcp_client.jobs import BackgroundJobManager
from oem_mcp_client.logging_setup import configure_logging, tail_log
from oem_mcp_client.metrics import candidate_tools, collect_local_metrics
from oem_mcp_client.policy import PolicyEngine
from oem_mcp_client.safety import ToolSafetyError, redact, risk_label, validate_tool_call
from oem_mcp_client.ui_helpers import render_tool_result, schema_arguments, tool_table
from oem_mcp_client.workspace import WorkspaceStore

st.set_page_config(page_title="OEM MCP Client", page_icon="🔗", layout="wide")

DATA_DIR, LOG_DIR, PROFILE_FILE = runtime_paths()
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = configure_logging(LOG_DIR)
LOGGER = logging.getLogger("oem_mcp_client.app")
HISTORY = HistoryStore(DATA_DIR / "history.sqlite3")
WORKSPACE = WorkspaceStore(DATA_DIR / "workspace.sqlite3")
PROFILES = ProfileStore(PROFILE_FILE)
try:
    POLICY = PolicyEngine.from_file(os.getenv("OEM_MCP_POLICY_FILE") or None)
    POLICY_ERROR = ""
except ValueError as exc:
    POLICY = PolicyEngine()
    POLICY_ERROR = str(exc)


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEFAULTS = {
    "conn_endpoint": os.getenv("OEM_MCP_ENDPOINT", "https://oem.example.com:7803/em/api/mcp"),
    "conn_username": os.getenv("OEM_MCP_USERNAME", ""),
    "conn_password": os.getenv("OEM_MCP_PASSWORD", ""),
    "conn_protocol": os.getenv("OEM_MCP_PROTOCOL_VERSION", SUPPORTED_PROTOCOLS[0])
    if os.getenv("OEM_MCP_PROTOCOL_VERSION", SUPPORTED_PROTOCOLS[0]) in SUPPORTED_PROTOCOLS
    else SUPPORTED_PROTOCOLS[0],
    "conn_verify_tls": os.getenv("OEM_MCP_VERIFY_TLS", "true").strip().lower() not in {"0", "false", "no", "off"},
    "conn_ca_bundle": os.getenv("OEM_MCP_CA_BUNDLE", ""),
    "conn_timeout": int(os.getenv("OEM_MCP_TIMEOUT_SECONDS", "60")),
    "client": None,
    "discovery": None,
    "last_result": None,
    "last_metric_host": None,
    "last_metric_database": None,
    "operator_id": os.getenv("OEM_MCP_OPERATOR_ID", "operator"),
    "operator_role": os.getenv("OEM_MCP_OPERATOR_ROLE", "operator"),
}
for state_key, default_value in DEFAULTS.items():
    st.session_state.setdefault(state_key, default_value)
if "job_manager" not in st.session_state:
    st.session_state["job_manager"] = BackgroundJobManager(WORKSPACE, max_workers=int(os.getenv("OEM_MCP_JOB_WORKERS", "2")))
JOBS: BackgroundJobManager = st.session_state["job_manager"]


def active_client() -> OemMcpClient | None:
    client = st.session_state.get("client")
    return client if isinstance(client, OemMcpClient) and client.initialized else None


def discovery() -> dict[str, Any]:
    value = st.session_state.get("discovery")
    return value if isinstance(value, dict) else {}


def selected_tool(tools: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((tool for tool in tools if tool.get("name") == name), None)


def record_failure(config: ConnectionConfig, event: str, exc: Exception, elapsed_ms: int | None = None) -> None:
    HISTORY.record_connection(
        endpoint=config.endpoint,
        username=config.username,
        protocol_version=config.protocol_version,
        event=event,
        status="error",
        latency_ms=elapsed_ms,
        message=type(exc).__name__,
    )


def authorized_call_with_client(
    client: OemMcpClient,
    operator_role: str,
    tool: dict[str, Any],
    arguments: dict[str, Any],
    *,
    context: str,
) -> dict[str, Any]:
    start = time.monotonic()
    try:
        decision = POLICY.evaluate(operator_role, tool, arguments)
        if not decision.allowed:
            raise ToolSafetyError(f"Policy denied the request ({decision.matched_rule}): {decision.reason}")
        if decision.requires_approval and not WORKSPACE.has_valid_approval(
            client.config.endpoint, str(tool.get("name", "")), arguments
        ):
            raise ToolSafetyError("This request requires an unexpired approval from a different operator. Create it in Governance.")
        validate_tool_call(
            tool,
            arguments,
            allow_mutating=env_flag("OEM_MCP_ALLOW_MUTATING_TOOLS"),
            allow_nonselect_sql=env_flag("OEM_MCP_ALLOW_NONSELECT_SQL"),
        )
        result = client.call_tool(str(tool.get("name", "")), arguments)
    except (McpClientError, ToolSafetyError, ValueError) as exc:
        elapsed_ms = round((time.monotonic() - start) * 1000)
        HISTORY.record_execution(
            endpoint=client.config.endpoint,
            tool_name=str(tool.get("name", "")),
            status="error",
            latency_ms=elapsed_ms,
            arguments=arguments,
            message=f"context={context};error={type(exc).__name__}",
        )
        WORKSPACE.record_usage(
            category="mcp",
            operation=str(tool.get("name", "")),
            provider="oracle-enterprise-manager",
            latency_ms=elapsed_ms,
            status="error",
            context=context,
        )
        raise
    elapsed_ms = round((time.monotonic() - start) * 1000)
    HISTORY.record_execution(
        endpoint=client.config.endpoint,
        tool_name=str(tool.get("name", "")),
        status="success" if not result.get("isError") else "tool-error",
        latency_ms=elapsed_ms,
        arguments=arguments,
        message=f"context={context};policy={decision.matched_rule}",
    )
    WORKSPACE.record_usage(
        category="mcp",
        operation=str(tool.get("name", "")),
        provider="oracle-enterprise-manager",
        latency_ms=elapsed_ms,
        status="success" if not result.get("isError") else "tool-error",
        context=context,
    )
    LOGGER.info(
        "Tool call completed name=%s context=%s elapsed_ms=%s is_error=%s policy=%s",
        tool.get("name"),
        context,
        elapsed_ms,
        bool(result.get("isError")),
        decision.matched_rule,
    )
    return result


def authorized_call(tool: dict[str, Any], arguments: dict[str, Any], *, context: str) -> dict[str, Any]:
    client = active_client()
    if not client:
        raise McpClientError("Connect and initialize the OEM MCP session first.")
    return authorized_call_with_client(
        client,
        str(st.session_state.get("operator_role", "operator")),
        tool,
        arguments,
        context=context,
    )


def execute_tool(tool: dict[str, Any], arguments: dict[str, Any], *, context: str) -> dict[str, Any] | None:
    try:
        return authorized_call(tool, arguments, context=context)
    except (McpClientError, ToolSafetyError, ValueError) as exc:
        LOGGER.warning("Tool call rejected or failed name=%s context=%s type=%s", tool.get("name"), context, type(exc).__name__)
        st.error(str(exc))
        return None


def advanced_execute(tool: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any] | None:
    result = execute_tool(tool, arguments, context="advanced-tab")
    if result is not None:
        st.session_state["last_result"] = result
    return result


def background_execute(tool: dict[str, Any], arguments: dict[str, Any], context: str) -> Callable[[], dict[str, Any]]:
    captured_client = active_client()
    captured_role = str(st.session_state.get("operator_role", "operator"))

    def run() -> dict[str, Any]:
        if not captured_client:
            raise McpClientError("The OEM MCP session was not available when this job was queued.")
        return authorized_call_with_client(captured_client, captured_role, tool, arguments, context=context)

    return run


def render_metric_tool(domain: str, title: str) -> None:
    tools = list(discovery().get("tools") or [])
    candidates = candidate_tools(tools, domain)
    if not candidates:
        st.info(
            f"No authorized {domain} metric candidates were discovered. "
            "Review the Capabilities tab or ask the OEM administrator to grant the required operation privileges."
        )
        return
    names = [str(tool.get("name", "")) for tool in candidates]
    name = st.selectbox(f"{title} tool", names, key=f"metric-{domain}-tool")
    tool = selected_tool(candidates, name)
    if not tool:
        return
    st.caption(str(tool.get("description") or "No description returned by the MCP server."))
    st.caption(f"Safety classification: {risk_label(tool)}")
    with st.form(f"metric-{domain}-form"):
        arguments, errors = schema_arguments(dict(tool.get("inputSchema") or {}), key_prefix=f"metric-{domain}")
        confirm = st.checkbox("I confirm this request is authorized and the arguments are correct.", key=f"metric-{domain}-confirm")
        submitted = st.form_submit_button(f"Run {title} request")
    if submitted:
        if errors:
            st.error("\n".join(errors))
        elif not confirm:
            st.error("Confirmation is required before invoking an OEM operation.")
        else:
            with st.spinner("Calling the OEM MCP tool..."):
                result = execute_tool(tool, arguments, context=f"metrics-{domain}")
            if result is not None:
                st.session_state[f"last_metric_{domain}"] = result
    previous = st.session_state.get(f"last_metric_{domain}")
    if isinstance(previous, dict):
        render_tool_result(previous)


st.title("Oracle Enterprise Manager MCP Client")
st.caption(
    "Discover and invoke the operations authorized for your Enterprise Manager account, "
    "with local Linux observability and an auditable connection history."
)
if not env_flag("OEM_MCP_REVERSE_PROXY_AUTHENTICATED"):
    st.warning(
        "This client handles an OEM Basic Authentication password in memory. Bind it to loopback and place it behind an "
        "authenticated TLS reverse proxy before shared use."
    )

with st.sidebar:
    st.subheader("Session")
    client = active_client()
    if client:
        st.success("Initialized")
        st.caption(safe_endpoint(client.config.endpoint))
        st.caption(f"Protocol: {client.negotiated_protocol}")
        if client.session_id:
            st.caption(f"Session: …{client.session_id[-6:]}")
    else:
        st.info("Not connected")
    st.divider()
    st.markdown(
        "[Oracle OEM MCP documentation](https://docs.oracle.com/en/enterprise-manager/cloud-control/enterprise-manager-cloud-control/24.1/emadm/enterprise-manager-model-context-protocol-server.html)"
    )

(
    connection_tab,
    capabilities_tab,
    request_tab,
    metrics_tab,
    operations_tab,
    workspace_tab,
    topology_tab,
    sql_tab,
    incidents_tab,
    assistant_tab,
    governance_tab,
    automation_tab,
    usage_tab,
    history_tab,
    diagnostics_tab,
) = st.tabs(
    [
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
    ]
)

with connection_tab:
    st.subheader("OEM MCP connection")
    saved_profiles = PROFILES.load()
    if saved_profiles:
        profile_name = st.selectbox("Saved non-secret profile", ["—"] + sorted(saved_profiles), key="selected-profile")
        if st.button("Load profile", disabled=profile_name == "—"):
            profile = saved_profiles[profile_name]
            st.session_state["conn_endpoint"] = profile.get("endpoint", st.session_state["conn_endpoint"])
            st.session_state["conn_username"] = profile.get("username", "")
            st.session_state["conn_protocol"] = profile.get("protocol_version", SUPPORTED_PROTOCOLS[0])
            st.session_state["conn_verify_tls"] = bool(profile.get("verify_tls", True))
            st.session_state["conn_ca_bundle"] = profile.get("ca_bundle", "")
            st.session_state["conn_timeout"] = int(profile.get("timeout_seconds", 60))
            st.session_state["conn_password"] = ""
            st.rerun()

    with st.form("connection-form"):
        endpoint = st.text_input("OEM MCP endpoint", key="conn_endpoint", help="Example: https://oem.example.com:7803/em/api/mcp")
        col_user, col_password = st.columns(2)
        with col_user:
            username = st.text_input("Enterprise Manager username", key="conn_username")
        with col_password:
            password = st.text_input(
                "Enterprise Manager password",
                type="password",
                key="conn_password",
                help="Kept only in the Streamlit process session; never written to profiles, logs, or history.",
            )
        col_protocol, col_timeout = st.columns(2)
        with col_protocol:
            protocol = st.selectbox("MCP protocol version", SUPPORTED_PROTOCOLS, key="conn_protocol")
        with col_timeout:
            timeout = st.number_input("Request timeout (seconds)", min_value=5, max_value=600, step=5, key="conn_timeout")
        verify_tls = st.checkbox("Verify the OEM TLS certificate", key="conn_verify_tls")
        ca_bundle = st.text_input("Custom CA bundle path (optional)", key="conn_ca_bundle")
        save_profile = st.checkbox("Save this non-secret profile")
        profile_to_save = st.text_input("Profile name", placeholder="production-oem", disabled=not save_profile)
        connect_submitted = st.form_submit_button("Connect, initialize, and discover")

    if not verify_tls:
        st.error("TLS verification is disabled. Use only for isolated debugging and never for production credentials.")
    if connect_submitted:
        config = ConnectionConfig(
            endpoint=endpoint,
            username=username,
            protocol_version=protocol,
            verify_tls=verify_tls,
            ca_bundle=ca_bundle,
            timeout_seconds=int(timeout),
        )
        start = time.monotonic()
        try:
            old_client = active_client()
            if old_client:
                old_client.close()
            client = OemMcpClient(config, password, allow_http=env_flag("OEM_MCP_ALLOW_HTTP"))
            with st.spinner("Initializing MCP and discovering the authorized server surface..."):
                client.initialize()
                discovered = client.discover_all()
            elapsed_ms = round((time.monotonic() - start) * 1000)
            st.session_state["client"] = client
            st.session_state["discovery"] = discovered
            HISTORY.record_connection(
                endpoint=client.config.endpoint,
                username=client.config.username,
                protocol_version=client.negotiated_protocol,
                event="connect+discover",
                status="success",
                latency_ms=elapsed_ms,
                tool_count=len(discovered.get("tools") or []),
                message=f"optional_list_errors={len(discovered.get('errors') or {})}",
            )
            if save_profile:
                PROFILES.save(profile_to_save, client.config)
            LOGGER.info(
                "MCP session initialized endpoint=%s tools=%s", safe_endpoint(client.config.endpoint), len(discovered.get("tools") or [])
            )
            st.success(f"Connected. Discovered {len(discovered.get('tools') or [])} authorized tools.")
        except (McpClientError, ValueError) as exc:
            elapsed_ms = round((time.monotonic() - start) * 1000)
            try:
                clean = config.validated(allow_http=env_flag("OEM_MCP_ALLOW_HTTP"))
                record_failure(clean, "connect+discover", exc, elapsed_ms)
            except ValueError:
                pass
            LOGGER.warning("Connection failed type=%s", type(exc).__name__)
            st.error(str(exc))

    client = active_client()
    if client:
        col_disconnect, col_state = st.columns([1, 3])
        with col_disconnect:
            if st.button("Disconnect"):
                HISTORY.record_connection(
                    endpoint=client.config.endpoint,
                    username=client.config.username,
                    protocol_version=client.negotiated_protocol,
                    event="disconnect",
                    status="success",
                )
                client.close(terminate_remote=True)
                st.session_state["client"] = None
                st.session_state["discovery"] = None
                st.rerun()
        with col_state:
            st.json(
                {
                    "endpoint": safe_endpoint(client.config.endpoint),
                    "protocol": client.negotiated_protocol,
                    "serverInfo": client.server_info,
                }
            )

with capabilities_tab:
    st.subheader("Discovered OEM MCP surface")
    client = active_client()
    if not client:
        st.info("Connect in the Connection tab to retrieve capabilities and options.")
    else:
        if st.button("Refresh all discovery lists"):
            try:
                with st.spinner("Refreshing tools, prompts, resources, and resource templates..."):
                    st.session_state["discovery"] = client.discover_all()
                st.success("Discovery refreshed.")
            except McpClientError as exc:
                st.error(str(exc))
        found = discovery()
        tools = list(found.get("tools") or [])
        counts = st.columns(4)
        counts[0].metric("Tools", len(tools))
        counts[1].metric("Prompts", len(found.get("prompts") or []))
        counts[2].metric("Resources", len(found.get("resources") or []))
        counts[3].metric("Resource templates", len(found.get("resourceTemplates") or []))
        if found.get("errors"):
            st.warning("Some optional discovery calls failed. This can be normal when an OEM release does not expose those capabilities.")
            st.json(found["errors"])
        if tools:
            st.dataframe(tool_table(tools), width="stretch", hide_index=True)
            inspect_name = st.selectbox("Inspect a tool", [str(tool.get("name", "")) for tool in tools], key="capability-tool")
            tool = selected_tool(tools, inspect_name)
            if tool:
                st.caption(f"Safety classification: {risk_label(tool)}")
                st.json(tool)
        else:
            st.warning("The server returned no tools for this account.")
        with st.expander("Server information and negotiated capabilities"):
            st.json(
                {
                    "protocolVersion": found.get("protocolVersion"),
                    "serverInfo": found.get("serverInfo"),
                    "capabilities": found.get("serverCapabilities"),
                    "instructions": found.get("instructions"),
                }
            )

with request_tab:
    st.subheader("Invoke an authorized OEM MCP tool")
    client = active_client()
    tools = list(discovery().get("tools") or [])
    if not client or not tools:
        st.info("Connect and discover tools before executing a request.")
    else:
        name = st.selectbox("Tool", [str(tool.get("name", "")) for tool in tools], key="request-tool")
        tool = selected_tool(tools, name)
        if tool:
            st.write(str(tool.get("description") or "No description returned by the MCP server."))
            st.caption(f"Safety classification: {risk_label(tool)}")
            with st.expander("Input schema"):
                st.json(tool.get("inputSchema") or {})
            with st.form("tool-request-form"):
                arguments, errors = schema_arguments(dict(tool.get("inputSchema") or {}), key_prefix="request")
                confirm = st.checkbox("I confirm that I am authorized to run this operation and have reviewed every argument.")
                submitted = st.form_submit_button("Execute MCP request")
            if submitted:
                if errors:
                    st.error("\n".join(errors))
                elif not confirm:
                    st.error("Confirmation is required before invoking an OEM operation.")
                else:
                    with st.spinner("Invoking the OEM MCP tool..."):
                        result = execute_tool(tool, arguments, context="request-tab")
                    if result is not None:
                        st.session_state["last_result"] = result
            if isinstance(st.session_state.get("last_result"), dict):
                render_tool_result(st.session_state["last_result"])

with metrics_tab:
    st.subheader("Linux and OEM-managed target metrics")
    local_tab, host_tab, database_tab, association_tab = st.tabs(
        ["Streamlit Linux host", "OEM Linux targets", "OEM databases", "Host ↔ database associations"]
    )
    with local_tab:
        local = collect_local_metrics()
        cols = st.columns(4)
        cols[0].metric("CPU", f"{local['host']['cpu_percent']:.1f}%")
        cols[1].metric("Load (1m)", local["host"]["load_1"])
        cols[2].metric("Memory", f"{local['memory']['percent']:.1f}%")
        cols[3].metric("Disk", f"{local['disk']['percent']:.1f}%")
        memory_df = pd.DataFrame(
            [
                {
                    "area": "Memory",
                    "used_gib": local["memory"]["used_gib"],
                    "total_gib": local["memory"]["total_gib"],
                    "percent": local["memory"]["percent"],
                },
                {
                    "area": "Swap",
                    "used_gib": local["memory"]["swap_used_gib"],
                    "total_gib": None,
                    "percent": local["memory"]["swap_percent"],
                },
                {
                    "area": "Disk",
                    "used_gib": local["disk"]["used_gib"],
                    "total_gib": local["disk"]["total_gib"],
                    "percent": local["disk"]["percent"],
                },
            ]
        )
        st.dataframe(memory_df, width="stretch", hide_index=True)
        with st.expander("Raw local metrics"):
            st.json(local)
    with host_tab:
        if not active_client():
            st.info("Connect to OEM to discover authorized host and metric operations.")
        else:
            render_metric_tool("host", "OEM Linux target metrics")
    with database_tab:
        if not active_client():
            st.info("Connect to OEM to discover authorized database, repository, and SQL operations.")
        else:
            render_metric_tool("database", "OEM database metrics")
    with association_tab:
        if not active_client():
            st.info("Connect to OEM to discover authorized target relationship and topology operations.")
        else:
            render_metric_tool("association", "OEM host-to-database relationships")

with operations_tab:
    render_operations(discovery(), advanced_execute, WORKSPACE)

with workspace_tab:
    render_workspace(discovery(), WORKSPACE, PROFILES)

with topology_tab:
    render_topology(discovery(), advanced_execute)

with sql_tab:
    render_sql(discovery(), advanced_execute, WORKSPACE)

with incidents_tab:
    render_incidents(WORKSPACE)

with assistant_tab:
    render_assistant(discovery(), advanced_execute, WORKSPACE)

with governance_tab:
    if POLICY_ERROR:
        st.error(f"Policy configuration error; built-in read-only policy is active: {POLICY_ERROR}")
    render_governance(
        discovery(),
        WORKSPACE,
        POLICY,
        active_client().config.endpoint if active_client() else "https://not-connected.invalid/em/api/mcp",
    )

with automation_tab:
    render_automation(discovery(), WORKSPACE, JOBS, background_execute)

with usage_tab:
    render_usage(WORKSPACE)

with history_tab:
    st.subheader("Connection and execution history")
    limit = st.selectbox("Rows", [50, 100, 200, 500], index=2)
    connection_rows = HISTORY.recent_connections(limit)
    execution_rows = HISTORY.recent_executions(limit)
    history_connections, history_executions, file_log = st.tabs(["Connections", "Tool executions", "Application log"])
    with history_connections:
        st.dataframe(pd.DataFrame(connection_rows), width="stretch", hide_index=True)
        st.download_button(
            "Download connections JSON",
            json.dumps(connection_rows, indent=2),
            file_name="oem-mcp-connections.json",
            mime="application/json",
        )
    with history_executions:
        st.dataframe(pd.DataFrame(execution_rows), width="stretch", hide_index=True)
        st.download_button(
            "Download executions JSON", json.dumps(execution_rows, indent=2), file_name="oem-mcp-executions.json", mime="application/json"
        )
    with file_log:
        st.code(tail_log(LOG_PATH, 300), language=None)
        st.caption("Transport logs include methods, HTTP status, and timings only. Credentials and request bodies are not logged.")

with diagnostics_tab:
    st.subheader("Diagnostics")
    client = active_client()
    diagnostic = {
        "endpoint": safe_endpoint(client.config.endpoint) if client else None,
        "connected": bool(client),
        "protocol": client.negotiated_protocol if client else None,
        "session_id_present": bool(client and client.session_id),
        "server_info": client.server_info if client else {},
        "tool_count": len(discovery().get("tools") or []),
        "optional_discovery_errors": discovery().get("errors") or {},
        "data_dir": str(DATA_DIR),
        "log_dir": str(LOG_DIR),
        "profile_file": str(PROFILE_FILE),
        "allow_http": env_flag("OEM_MCP_ALLOW_HTTP"),
        "allow_mutating_tools": env_flag("OEM_MCP_ALLOW_MUTATING_TOOLS"),
        "allow_nonselect_sql": env_flag("OEM_MCP_ALLOW_NONSELECT_SQL"),
        "reverse_proxy_authenticated": env_flag("OEM_MCP_REVERSE_PROXY_AUTHENTICATED"),
        "policy_file": os.getenv("OEM_MCP_POLICY_FILE", "built-in read-only"),
        "operator_role": st.session_state.get("operator_role"),
        "workspace_database": str(DATA_DIR / "workspace.sqlite3"),
        "background_workers": int(os.getenv("OEM_MCP_JOB_WORKERS", "2")),
        "oci_genai_configured": bool(os.getenv("OCI_GENAI_OPENAI_ENDPOINT") and os.getenv("OCI_GENAI_MODEL")),
    }
    st.json(redact(diagnostic))
    if st.button("Send MCP ping", disabled=not bool(client)):
        try:
            started = time.monotonic()
            result = client.ping() if client else {}
            st.success(f"Ping succeeded in {round((time.monotonic() - started) * 1000)} ms")
            st.json(result)
        except McpClientError as exc:
            st.error(str(exc))
    st.download_button(
        "Download redacted diagnostics",
        json.dumps(redact(diagnostic), indent=2, default=str),
        file_name="oem-mcp-diagnostics.json",
        mime="application/json",
    )
    st.code("scripts/diagnose.sh --connect --env-file .runtime/oem-mcp-streamlit.env", language="bash")
