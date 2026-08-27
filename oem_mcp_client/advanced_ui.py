from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd
import requests
import streamlit as st

from .assistant import OciGenAiPlanner, estimated_cost, local_plan, rank_tools
from .client import McpClientError, OemMcpClient
from .config import SUPPORTED_PROTOCOLS, ConnectionConfig, ProfileStore
from .jobs import BackgroundJobManager
from .metrics import candidate_tools, collect_local_metrics
from .operations import correlate_incident, health_score, infer_topology, result_rows, topology_dot
from .policy import PolicyEngine
from .safety import ToolSafetyError, bounded_read_only_sql, risk_label
from .ui_helpers import render_tool_result
from .workspace import WorkspaceStore

ExecuteCallback = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None]
BackgroundExecuteCallback = Callable[[dict[str, Any], dict[str, Any], str], Callable[[], dict[str, Any]]]


def _tools(discovered: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in discovered.get("tools") or [] if isinstance(item, dict)]


def _tool(discovered: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((item for item in _tools(discovered) if str(item.get("name", "")) == name), None)


def _json_object(raw: str) -> tuple[dict[str, Any], str]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return {}, f"Invalid JSON: {exc.msg}"
    if not isinstance(value, dict):
        return {}, "Arguments must be a JSON object."
    return value, ""


def _result_dataframe(result: dict[str, Any] | None) -> pd.DataFrame:
    return pd.DataFrame(result_rows(result or {}))


@st.fragment(run_every="5s")
def _live_local_panel() -> None:
    metrics = collect_local_metrics()
    score = health_score(metrics["host"]["cpu_percent"], metrics["memory"]["percent"], metrics["disk"]["percent"])
    columns = st.columns(5)
    columns[0].metric("Health", f"{score}/100")
    columns[1].metric("CPU", f"{metrics['host']['cpu_percent']:.1f}%")
    columns[2].metric("Load 1m", metrics["host"]["load_1"])
    columns[3].metric("Memory", f"{metrics['memory']['percent']:.1f}%")
    columns[4].metric("Disk", f"{metrics['disk']['percent']:.1f}%")
    st.caption(f"Live local sample: {datetime.now(timezone.utc).isoformat(timespec='seconds')}; refreshes every 5 seconds.")


def render_operations(
    discovered: dict[str, Any],
    execute: ExecuteCallback,
    workspace: WorkspaceStore,
) -> None:
    st.subheader("Live operations dashboard")
    _live_local_panel()
    st.divider()
    tools = _tools(discovered)
    metric_tools = candidate_tools(tools, "host") + candidate_tools(tools, "database")
    names = list(dict.fromkeys(str(item.get("name", "")) for item in metric_tools if item.get("name")))
    if not names:
        st.info("Connect to OEM to run a live authorized host or database metric operation.")
        return
    name = st.selectbox("OEM live metric tool", names, key="ops-live-tool")
    arguments_raw = st.text_area("Arguments (JSON)", "{}", key="ops-live-args")
    if st.button("Run live OEM sample", key="ops-live-run"):
        arguments, error = _json_object(arguments_raw)
        if error:
            st.error(error)
        elif tool := _tool(discovered, name):
            result = execute(tool, arguments)
            if result:
                st.session_state["ops_live_result"] = result
    result = st.session_state.get("ops_live_result")
    if isinstance(result, dict):
        frame = _result_dataframe(result)
        if not frame.empty:
            numeric = frame.select_dtypes(include="number")
            if not numeric.empty:
                st.line_chart(numeric)
            st.dataframe(frame, width="stretch", hide_index=True)
        with st.expander("Raw live result"):
            render_tool_result(result)
        dashboard_name = st.text_input("Save this view as dashboard", key="ops-dashboard-name")
        if st.button("Save dashboard", disabled=not dashboard_name.strip(), key="ops-dashboard-save"):
            workspace.save_artifact("dashboard", dashboard_name, {"tool": name, "arguments": arguments_raw, "result": result})
            st.success("Dashboard snapshot saved.")


def render_workspace(
    discovered: dict[str, Any],
    workspace: WorkspaceStore,
    profile_store: ProfileStore,
) -> None:
    st.subheader("Saved dashboards, runbooks, and OEM fleet")
    dashboards_tab, runbooks_tab, fleet_tab = st.tabs(["Dashboards", "Runbooks", "Multi-OEM fleet"])
    with dashboards_tab:
        dashboards = workspace.list_artifacts("dashboard")
        st.dataframe(pd.DataFrame(dashboards), width="stretch", hide_index=True)
        if dashboards:
            chosen = st.selectbox("Inspect dashboard", [row["name"] for row in dashboards], key="workspace-dashboard")
            row = next(item for item in dashboards if item["name"] == chosen)
            st.json(json.loads(row["payload_json"]))
    with runbooks_tab:
        with st.form("runbook-save"):
            name = st.text_input("Runbook name")
            description = st.text_input("Purpose")
            steps = st.text_area("Steps (one per line)", height=180)
            saved = st.form_submit_button("Save or update runbook")
        if saved:
            workspace.save_artifact(
                "runbook",
                name,
                {"steps": [line.strip() for line in steps.splitlines() if line.strip()]},
                description,
            )
            st.success("Runbook saved.")
        runbooks = workspace.list_artifacts("runbook")
        for row in runbooks:
            with st.expander(f"{row['name']} — {row['description']}"):
                for index, step in enumerate(json.loads(row["payload_json"]).get("steps", []), start=1):
                    st.checkbox(step, key=f"runbook-{row['id']}-{index}")
    with fleet_tab:
        profiles = profile_store.load()
        rows = [
            {
                "profile": name,
                "endpoint": profile.get("endpoint"),
                "username": profile.get("username"),
                "protocol": profile.get("protocol_version"),
                "connected": name in st.session_state.get("fleet_clients", {}),
                "tool_count": len(st.session_state.get("fleet_discovery", {}).get(name, {}).get("tools") or []),
            }
            for name, profile in profiles.items()
        ]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        if not profiles:
            st.info("Save non-secret OEM profiles in Connection first.")
        else:
            with st.form("fleet-connect"):
                name = st.selectbox("OEM profile", sorted(profiles), key="fleet-profile")
                password = st.text_input("Password for this OEM", type="password", key="fleet-password")
                connect = st.form_submit_button("Connect profile into fleet")
            if connect:
                profile = profiles[name]
                try:
                    config = ConnectionConfig(
                        endpoint=str(profile.get("endpoint", "")),
                        username=str(profile.get("username", "")),
                        protocol_version=str(profile.get("protocol_version", SUPPORTED_PROTOCOLS[0])),
                        verify_tls=bool(profile.get("verify_tls", True)),
                        ca_bundle=str(profile.get("ca_bundle", "")),
                        timeout_seconds=int(profile.get("timeout_seconds", 60)),
                    )
                    client = OemMcpClient(config, password, allow_http=os.getenv("OEM_MCP_ALLOW_HTTP", "").lower() == "true")
                    client.initialize()
                    fleet_clients = st.session_state.setdefault("fleet_clients", {})
                    fleet_discovery = st.session_state.setdefault("fleet_discovery", {})
                    fleet_clients[name] = client
                    fleet_discovery[name] = client.discover_all()
                    st.success(f"Connected {name}; {len(fleet_discovery[name].get('tools') or [])} tools discovered.")
                except (McpClientError, ValueError) as exc:
                    st.error(str(exc))
            connected = st.session_state.get("fleet_discovery", {})
            if connected:
                matrix = sorted({str(tool.get("name", "")) for value in connected.values() for tool in value.get("tools") or []})
                comparison = [
                    {
                        "tool": tool_name,
                        **{
                            name: any(tool.get("name") == tool_name for tool in data.get("tools") or [])
                            for name, data in connected.items()
                        },
                    }
                    for tool_name in matrix
                ]
                st.dataframe(pd.DataFrame(comparison), width="stretch", hide_index=True)


def render_topology(discovered: dict[str, Any], execute: ExecuteCallback) -> None:
    st.subheader("OEM topology explorer")
    candidates = candidate_tools(_tools(discovered), "association")
    if candidates:
        names = [str(item.get("name", "")) for item in candidates]
        name = st.selectbox("Relationship/topology tool", names, key="topology-tool")
        raw = st.text_area("Arguments (JSON)", "{}", key="topology-args")
        if st.button("Retrieve topology", key="topology-run"):
            arguments, error = _json_object(raw)
            if error:
                st.error(error)
            elif tool := _tool(discovered, name):
                result = execute(tool, arguments)
                if result:
                    st.session_state["topology_result"] = result
    else:
        st.info("Connect to discover an authorized topology or target-association operation.")
    source = st.session_state.get("topology_result") or st.session_state.get("last_result")
    if isinstance(source, dict):
        topology = infer_topology(source)
        columns = st.columns(2)
        columns[0].metric("Nodes", len(topology.nodes))
        columns[1].metric("Relationships", len(topology.edges))
        if topology.nodes:
            st.graphviz_chart(topology_dot(topology), use_container_width=True)
            st.dataframe(pd.DataFrame(topology.nodes), width="stretch", hide_index=True)
        else:
            st.warning("No common target/host/database relationship fields were found in the result.")


def render_sql(discovered: dict[str, Any], execute: ExecuteCallback, workspace: WorkspaceStore) -> None:
    st.subheader("Read-only SQL workbench")
    sql_tool = next((tool for tool in _tools(discovered) if str(tool.get("name", "")).casefold() == "executesql"), None)
    saved = workspace.list_artifacts("sql-query")
    selected = st.selectbox("Saved query", ["—"] + [row["name"] for row in saved], key="sql-saved")
    default_sql = "SELECT target_name, target_type FROM mgmt$target"
    if selected != "—":
        row = next(item for item in saved if item["name"] == selected)
        default_sql = str(json.loads(row["payload_json"]).get("sql", default_sql))
    sql = st.text_area("Oracle SQL", value=default_sql, height=220, key="sql-workbench")
    row_limit = st.number_input("Maximum rows", min_value=1, max_value=10_000, value=500, step=50)
    query_name = st.text_input("Query library name", key="sql-name")
    save_col, run_col = st.columns(2)
    if save_col.button("Save query", disabled=not query_name.strip(), key="sql-save"):
        try:
            bounded = bounded_read_only_sql(sql, int(row_limit))
            workspace.save_artifact("sql-query", query_name, {"sql": bounded, "row_limit": int(row_limit)})
            st.success("Query saved.")
        except ToolSafetyError as exc:
            st.error(str(exc))
    if run_col.button("Validate and run", disabled=sql_tool is None, key="sql-run"):
        try:
            bounded = bounded_read_only_sql(sql, int(row_limit))
            schema = sql_tool.get("inputSchema") if isinstance(sql_tool, dict) else {}
            properties = schema.get("properties") if isinstance(schema, dict) else {}
            sql_key = next((name for name in properties if any(part in str(name).lower() for part in ("sql", "query", "statement"))), "sql")
            result = execute(sql_tool or {}, {sql_key: bounded})
            if result:
                st.session_state["sql_result"] = result
        except ToolSafetyError as exc:
            st.error(str(exc))
    if sql_tool is None:
        st.info("OEM did not advertise ExecuteSql for the connected account.")
    result = st.session_state.get("sql_result")
    if isinstance(result, dict):
        render_tool_result(result)


def render_incidents(workspace: WorkspaceStore) -> None:
    st.subheader("Incident investigation workspace")
    create_tab, investigate_tab = st.tabs(["Create", "Investigate"])
    with create_tab:
        with st.form("incident-create"):
            title = st.text_input("Incident title")
            severity = st.selectbox("Severity", ["critical", "high", "medium", "low"])
            summary = st.text_area("Initial summary")
            actor = st.text_input("Operator", value=st.session_state.get("operator_id", "operator"))
            create = st.form_submit_button("Create incident")
        if create:
            incident_id = workspace.create_incident(title, severity, summary, actor)
            st.success(f"Incident #{incident_id} created.")
    with investigate_tab:
        incidents = workspace.list_incidents()
        if not incidents:
            st.info("No incidents have been created.")
            return
        labels = {f"#{row['id']} {row['title']} [{row['status']}]": row for row in incidents}
        label = st.selectbox("Incident", list(labels), key="incident-selected")
        incident = labels[label]
        st.write(incident["summary"])
        st.dataframe(pd.DataFrame(json.loads(incident["timeline_json"])), width="stretch", hide_index=True)
        keywords = st.text_input("Correlate last MCP result (comma-separated terms)", key="incident-keywords")
        last_result = st.session_state.get("last_result") or st.session_state.get("ops_live_result")
        if isinstance(last_result, dict):
            matches = correlate_incident(result_rows(last_result), keywords.split(","))
            st.dataframe(pd.DataFrame(matches), width="stretch", hide_index=True)
        with st.form("incident-update"):
            actor = st.text_input("Operator", value=st.session_state.get("operator_id", "operator"), key="incident-actor")
            event = st.selectbox("Event", ["observation", "hypothesis", "action", "resolution"])
            note = st.text_area("Timeline note")
            status = st.selectbox("Status", [incident["status"], "open", "investigating", "monitoring", "resolved"])
            append = st.form_submit_button("Append timeline entry")
        if append:
            workspace.append_incident(int(incident["id"]), actor, event, note, status=status)
            st.success("Timeline updated.")


def render_assistant(discovered: dict[str, Any], execute: ExecuteCallback, workspace: WorkspaceStore) -> None:
    st.subheader("Natural-language assistant")
    st.caption("The assistant proposes one discovered tool and arguments. It cannot bypass policy or execute without a separate click.")
    provider = st.radio("Planner", ["Local deterministic", "OCI Generative AI"], horizontal=True)
    prompt = st.text_area("Operational request", height=140, placeholder="Show critical database incidents from the last hour")
    if st.button("Build reviewed proposal", key="assistant-plan"):
        try:
            if provider == "OCI Generative AI":
                endpoint = os.getenv("OCI_GENAI_OPENAI_ENDPOINT", "")
                api_key = os.getenv("OCI_GENAI_API_KEY", "")
                model = os.getenv("OCI_GENAI_MODEL", "")
                planner = OciGenAiPlanner(endpoint, api_key, model)
                plan = planner.plan(prompt, _tools(discovered))
            else:
                plan = local_plan(prompt, _tools(discovered))
            st.session_state["assistant_plan"] = plan
            cost = estimated_cost(
                plan.input_tokens,
                plan.output_tokens,
                float(os.getenv("OCI_GENAI_INPUT_USD_PER_MILLION", "0")),
                float(os.getenv("OCI_GENAI_OUTPUT_USD_PER_MILLION", "0")),
            )
            workspace.record_usage(
                category="assistant",
                operation="plan",
                provider=plan.provider,
                model=plan.model,
                input_tokens=plan.input_tokens,
                output_tokens=plan.output_tokens,
                estimated_cost=cost,
                latency_ms=plan.latency_ms,
            )
        except (McpClientError, requests.RequestException, ValueError) as exc:
            st.error(str(exc))
    plan = st.session_state.get("assistant_plan")
    if plan:
        st.json(
            {
                "tool_name": plan.tool_name,
                "arguments": plan.arguments,
                "explanation": plan.explanation,
                "confidence": plan.confidence,
                "provider": plan.provider,
                "model": plan.model,
            }
        )
        if st.button("Execute reviewed proposal", key="assistant-execute"):
            tool = _tool(discovered, plan.tool_name)
            if tool:
                result = execute(tool, plan.arguments)
                if result:
                    st.session_state["assistant_result"] = result
        if isinstance(st.session_state.get("assistant_result"), dict):
            render_tool_result(st.session_state["assistant_result"])
    if prompt:
        ranking = [
            {"score": score, "tool": tool.get("name"), "description": tool.get("description")}
            for score, tool in rank_tools(prompt, _tools(discovered))[:5]
        ]
        with st.expander("Top deterministic matches"):
            st.dataframe(pd.DataFrame(ranking), width="stretch", hide_index=True)


def render_governance(discovered: dict[str, Any], workspace: WorkspaceStore, policy: PolicyEngine, endpoint: str) -> None:
    st.subheader("Tool policy and two-person approvals")
    identity_col, role_col = st.columns(2)
    operator = identity_col.text_input(
        "Operator identity", value=st.session_state.get("operator_id", "operator"), key="governance-operator"
    )
    role = role_col.selectbox("Policy role", ["operator", "senior-operator", "approver", "viewer"], key="governance-role")
    st.session_state["operator_id"] = operator
    st.session_state["operator_role"] = role
    tools = _tools(discovered)
    if tools:
        name = st.selectbox("Evaluate/request tool", [str(tool.get("name", "")) for tool in tools], key="governance-tool")
        raw = st.text_area("Arguments (JSON)", "{}", key="governance-args")
        arguments, error = _json_object(raw)
        tool = _tool(discovered, name)
        if tool and not error:
            decision = policy.evaluate(role, tool, arguments)
            st.info(f"{decision.matched_rule}: {decision.reason} Allowed={decision.allowed}; approval={decision.requires_approval}.")
            if st.button("Create approval request", disabled=not decision.requires_approval, key="approval-create"):
                approval_id = workspace.create_approval(endpoint, name, arguments, operator)
                st.success(f"Approval request #{approval_id} created.")
    approvals = workspace.list_approvals()
    st.dataframe(pd.DataFrame(approvals), width="stretch", hide_index=True)
    pending = [row for row in approvals if row["status"] == "pending"]
    if pending:
        with st.form("approval-decision"):
            approval_id = st.selectbox("Pending approval", [int(row["id"]) for row in pending])
            approver = st.text_input("Approver identity")
            approve = st.radio("Decision", ["approve", "reject"], horizontal=True)
            note = st.text_input("Decision note")
            submit = st.form_submit_button("Record decision")
        if submit:
            try:
                workspace.decide_approval(approval_id, approver, approve == "approve", note)
                st.success("Decision recorded.")
            except ValueError as exc:
                st.error(str(exc))


def render_automation(
    discovered: dict[str, Any],
    workspace: WorkspaceStore,
    manager: BackgroundJobManager,
    background_execute: BackgroundExecuteCallback,
) -> None:
    st.subheader("Background jobs, schedules, reports, and alerts")
    jobs_tab, schedules_tab, alerts_tab = st.tabs(["Jobs", "Schedules & reports", "Alerts"])
    tools = _tools(discovered)
    with jobs_tab:
        if tools:
            name = st.selectbox("Tool", [str(tool.get("name", "")) for tool in tools], key="job-tool")
            raw = st.text_area("Arguments (JSON)", "{}", key="job-args")
            if st.button("Queue background request", key="job-submit"):
                arguments, error = _json_object(raw)
                tool = _tool(discovered, name)
                if error:
                    st.error(error)
                elif tool:
                    job_id = manager.submit("mcp-tool", name, arguments, background_execute(tool, arguments, "background-job"))
                    st.success(f"Job #{job_id} queued.")
        else:
            st.info("Connect to OEM before queuing MCP jobs.")
        jobs = workspace.list_jobs()
        st.dataframe(pd.DataFrame(jobs), width="stretch", hide_index=True)
        if jobs:
            job = st.selectbox("Inspect job", [int(row["id"]) for row in jobs], key="job-inspect")
            selected = next(row for row in jobs if int(row["id"]) == job)
            if selected.get("result_json"):
                st.download_button(
                    "Download report JSON",
                    selected["result_json"],
                    file_name=f"oem-mcp-job-{job}.json",
                    mime="application/json",
                )
    with schedules_tab:
        with st.form("schedule-save"):
            schedule_name = st.text_input("Schedule name")
            profile = st.text_input("OEM profile name", value="active-session")
            tool_name = st.selectbox("Read-only tool", [str(tool.get("name", "")) for tool in tools] or ["No tools"])
            arguments_raw = st.text_area("Arguments (JSON)", "{}", key="schedule-args")
            interval = st.number_input("Interval (minutes)", min_value=5, max_value=10080, value=60)
            save = st.form_submit_button("Save schedule")
        if save:
            arguments, error = _json_object(arguments_raw)
            tool = _tool(discovered, tool_name)
            if error:
                st.error(error)
            elif not tool or risk_label(tool) != "Read-only":
                st.error("Only a discovered read-only tool can be scheduled.")
            else:
                workspace.save_schedule(schedule_name, profile, tool_name, arguments, int(interval))
                st.success("Schedule saved. It runs only while an authenticated app session is active.")
        schedules = workspace.list_schedules()
        st.dataframe(pd.DataFrame(schedules), width="stretch", hide_index=True)
        if st.button("Queue all due schedules now", disabled=not bool(tools), key="schedule-due"):
            now = datetime.now(timezone.utc)
            queued = 0
            for schedule in workspace.list_schedules(enabled_only=True):
                due = datetime.fromisoformat(schedule["next_run_utc"]) <= now
                tool = _tool(discovered, schedule["tool_name"])
                if due and tool:
                    arguments = json.loads(schedule["arguments_json"])
                    manager.submit(
                        "scheduled-report",
                        schedule["name"],
                        arguments,
                        background_execute(tool, arguments, "scheduled-report"),
                    )
                    workspace.mark_schedule_run(int(schedule["id"]), int(schedule["interval_minutes"]))
                    queued += 1
            st.success(f"Queued {queued} due schedule(s).")
    with alerts_tab:
        local = collect_local_metrics()
        score = health_score(local["host"]["cpu_percent"], local["memory"]["percent"], local["disk"]["percent"])
        threshold = st.slider("Create alert when local health score is below", 0, 100, 60)
        if st.button("Evaluate alert rule", key="alert-evaluate"):
            if score < threshold:
                workspace.record_alert(None, "high", f"Local health score {score} is below threshold {threshold}.")
                st.error("Alert created.")
            else:
                st.success(f"No alert: health score is {score}.")
        st.dataframe(pd.DataFrame(workspace.list_alerts()), width="stretch", hide_index=True)
        st.caption(
            "External delivery is intentionally separate; integrate OCI Notifications or an allow-listed webhook at the platform layer."
        )


def render_usage(workspace: WorkspaceStore) -> None:
    st.subheader("Usage and estimated cost accounting")
    summary = workspace.usage_summary()
    columns = st.columns(5)
    columns[0].metric("Events", int(summary.get("events", 0)))
    columns[1].metric("Input tokens", int(summary.get("input_tokens", 0)))
    columns[2].metric("Output tokens", int(summary.get("output_tokens", 0)))
    columns[3].metric("Estimated cost", f"${float(summary.get('estimated_cost', 0)):.6f}")
    columns[4].metric("Avg latency", f"{float(summary.get('average_latency_ms', 0)):.0f} ms")
    rows = workspace.list_usage()
    frame = pd.DataFrame(rows)
    if not frame.empty:
        st.dataframe(frame, width="stretch", hide_index=True)
        grouped = frame.groupby(["category", "provider"], as_index=False)[["input_tokens", "output_tokens", "estimated_cost"]].sum()
        st.bar_chart(grouped.set_index("category")[["estimated_cost"]])
        st.download_button("Download usage CSV", frame.to_csv(index=False), "oem-mcp-usage.csv", "text/csv")
    st.caption("Costs are estimates from configured per-million-token prices; OCI billing and OEM audit records remain authoritative.")
