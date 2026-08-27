from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import psutil

from .safety import tool_is_read_only


def gib(value: int | float) -> float:
    return round(float(value) / (1024**3), 2)


def collect_local_metrics() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage(Path.home().anchor or "/")
    network = psutil.net_io_counters()
    process = psutil.Process(os.getpid())
    try:
        load_1, load_5, load_15 = os.getloadavg()
    except (AttributeError, OSError):
        load_1 = load_5 = load_15 = 0.0
    return {
        "timestamp_epoch": time.time(),
        "host": {
            "logical_cpus": psutil.cpu_count(logical=True) or 0,
            "physical_cpus": psutil.cpu_count(logical=False) or 0,
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "load_1": round(load_1, 2),
            "load_5": round(load_5, 2),
            "load_15": round(load_15, 2),
            "uptime_seconds": max(0, round(time.time() - psutil.boot_time())),
        },
        "memory": {
            "total_gib": gib(memory.total),
            "used_gib": gib(memory.used),
            "available_gib": gib(memory.available),
            "percent": memory.percent,
            "swap_used_gib": gib(swap.used),
            "swap_percent": swap.percent,
        },
        "disk": {"total_gib": gib(disk.total), "used_gib": gib(disk.used), "free_gib": gib(disk.free), "percent": disk.percent},
        "network": {"bytes_sent": network.bytes_sent, "bytes_received": network.bytes_recv},
        "process": {
            "pid": process.pid,
            "rss_gib": gib(process.memory_info().rss),
            "threads": process.num_threads(),
        },
    }


HOST_TERMS = ("host", "linux", "cpu", "memory", "filesystem", "load", "metric", "target", "status", "health")
DATABASE_TERMS = ("database", "oracle", "db", "sql", "session", "tablespace", "repository", "metric", "target", "health")
ASSOCIATION_TERMS = ("associate", "association", "relationship", "member", "host", "database", "target", "topology")


def candidate_tools(tools: list[dict[str, Any]], domain: str) -> list[dict[str, Any]]:
    terms = {"host": HOST_TERMS, "database": DATABASE_TERMS, "association": ASSOCIATION_TERMS}.get(domain, DATABASE_TERMS)
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for tool in tools:
        text = f"{tool.get('name', '')} {tool.get('title', '')} {tool.get('description', '')}".lower()
        score = sum(3 if term in str(tool.get("name", "")).lower() else 1 for term in terms if term in text)
        if domain == "database" and str(tool.get("name", "")).lower() == "executesql":
            score += 5
        if score and (tool_is_read_only(tool) or str(tool.get("name", "")).lower() == "executesql"):
            ranked.append((score, str(tool.get("name", "")), tool))
    ranked.sort(key=lambda row: (-row[0], row[1].lower()))
    return [tool for _, _, tool in ranked]
