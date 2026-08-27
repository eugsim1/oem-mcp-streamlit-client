from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any


def tabular_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return list(value)
    if isinstance(value, dict):
        for item in value.values():
            rows = tabular_rows(item)
            if rows:
                return rows
    return []


def result_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = tabular_rows(result.get("structuredContent"))
    if rows:
        return rows
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                rows = tabular_rows(json.loads(str(block.get("text", ""))))
            except (TypeError, ValueError):
                continue
            if rows:
                return rows
    return []


def health_score(cpu_percent: float, memory_percent: float, disk_percent: float) -> int:
    weighted = 0.4 * float(cpu_percent) + 0.35 * float(memory_percent) + 0.25 * float(disk_percent)
    return max(0, min(100, round(100 - weighted)))


def _first(record: dict[str, Any], patterns: tuple[str, ...]) -> str:
    for key, value in record.items():
        normalized = re.sub(r"[^a-z]", "", str(key).lower())
        if any(pattern in normalized for pattern in patterns) and value not in (None, ""):
            return str(value)
    return ""


@dataclass(frozen=True)
class Topology:
    nodes: list[dict[str, str]]
    edges: list[dict[str, str]]


def infer_topology(result: dict[str, Any]) -> Topology:
    nodes: dict[str, dict[str, str]] = {}
    edges: list[dict[str, str]] = []
    for record in result_rows(result):
        target = _first(record, ("targetname", "entityname", "membername", "name"))
        target_type = _first(record, ("targettype", "entitytype", "type")) or "target"
        host = _first(record, ("hostname", "hosttarget", "host"))
        database = _first(record, ("databasename", "dbtarget", "database", "db"))
        parent = _first(record, ("parenttarget", "parentname", "parent"))
        for node_id, kind in ((target, target_type), (host, "host"), (database, "database"), (parent, "parent")):
            if node_id:
                nodes[node_id] = {"id": node_id, "label": node_id, "kind": kind}
        if host and database:
            edges.append({"source": host, "target": database, "label": "hosts"})
        elif parent and target:
            edges.append({"source": parent, "target": target, "label": "contains"})
    return Topology(list(nodes.values()), edges)


def topology_dot(topology: Topology) -> str:
    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = ["digraph oem {", "rankdir=LR;", "node [shape=box style=rounded fontname=Arial];"]
    for node in topology.nodes:
        kind = node.get("kind", "target").lower()
        color = "#C74634" if "database" in kind or kind == "db" else "#1F6D8C" if "host" in kind else "#6B6B6B"
        label = html.escape(node.get("label", ""))
        lines.append(f"{quote(node['id'])} [label={quote(label)} color={quote(color)}];")
    for edge in topology.edges:
        lines.append(f"{quote(edge['source'])} -> {quote(edge['target'])} [label={quote(edge.get('label', ''))}];")
    lines.append("}")
    return "\n".join(lines)


def correlate_incident(rows: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    clean = [keyword.casefold() for keyword in keywords if keyword.strip()]
    if not clean:
        return rows
    return [row for row in rows if all(keyword in json.dumps(row, default=str).casefold() for keyword in clean)]
