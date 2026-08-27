from __future__ import annotations

import re
from typing import Any

SECRET_KEY_RE = re.compile(r"password|passwd|secret|token|authorization|credential|api[_-]?key|private[_-]?key", re.I)
READ_ONLY_RE = re.compile(r"(^|[_.-])(get|list|show|read|query|search|describe|status|health|metric|retrieve|find)([_.-]|$)", re.I)
READ_ONLY_PREFIXES = ("get", "list", "show", "read", "query", "search", "describe", "status", "health", "retrieve", "find")
MUTATING_PREFIXES = (
    "create",
    "update",
    "delete",
    "remove",
    "set",
    "acknowledge",
    "assign",
    "suppress",
    "stop",
    "start",
    "run",
    "execute",
    "apply",
)
MUTATING_RE = re.compile(
    r"(^|[_.-])(create|update|delete|remove|set|acknowledge|assign|suppress|stop|start|run|execute|apply)([_.-]|$)", re.I
)
SQL_MUTATION_RE = re.compile(
    r"\b(insert|update|delete|merge|alter|drop|truncate|grant|revoke|create|begin|declare|call|exec(?:ute)?)\b", re.I
)


class ToolSafetyError(ValueError):
    pass


def _starts_with_operation(name: str, operations: tuple[str, ...]) -> bool:
    lower_name = name.lower()
    for operation in operations:
        if lower_name == operation or lower_name.startswith((f"{operation}_", f"{operation}-", f"{operation}.")):
            return True
        if lower_name.startswith(operation) and len(name) > len(operation) and name[len(operation)].isupper():
            return True
    return False


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "***REDACTED***" if SECRET_KEY_RE.search(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def tool_is_read_only(tool: dict[str, Any]) -> bool:
    annotations = tool.get("annotations")
    if isinstance(annotations, dict) and annotations.get("readOnlyHint") is True:
        return True
    name = str(tool.get("name", ""))
    description = str(tool.get("description", ""))
    if MUTATING_RE.search(name) or _starts_with_operation(name, MUTATING_PREFIXES):
        return False
    return bool(
        READ_ONLY_RE.search(name)
        or _starts_with_operation(name, READ_ONLY_PREFIXES)
        or re.search(r"\b(read-only|retrieve|list|show|view|query)\b", description, re.I)
    )


def _strip_sql_comments(sql: str) -> str:
    without_blocks = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\r\n]*", " ", without_blocks).strip()


def is_read_only_sql(sql: str) -> bool:
    clean = _strip_sql_comments(sql)
    if not re.match(r"^(select|with)\b", clean, re.I):
        return False
    if SQL_MUTATION_RE.search(clean):
        return False
    statements = [part for part in clean.split(";") if part.strip()]
    return len(statements) == 1


def bounded_read_only_sql(sql: str, row_limit: int = 500) -> str:
    """Validate a query and append an Oracle row limit when one is absent."""
    if not is_read_only_sql(sql):
        raise ToolSafetyError("SQL workbench accepts one read-only SELECT/WITH statement.")
    limit = max(1, min(int(row_limit), 10_000))
    clean = sql.strip().rstrip(";")
    if re.search(r"\b(fetch\s+(?:first|next)|rownum\s*(?:<|<=|=)|offset\s+\d+)\b", clean, re.I):
        return clean
    return f"{clean}\nFETCH FIRST {limit} ROWS ONLY"


def _sql_values(value: Any, parent_key: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_sql_values(item, str(key)))
    elif isinstance(value, list):
        for item in value:
            found.extend(_sql_values(item, parent_key))
    elif isinstance(value, str) and re.search(r"sql|query|statement", parent_key, re.I):
        found.append(value)
    return found


def validate_tool_call(
    tool: dict[str, Any],
    arguments: dict[str, Any],
    *,
    allow_mutating: bool = False,
    allow_nonselect_sql: bool = False,
) -> None:
    name = str(tool.get("name", ""))
    if not name:
        raise ToolSafetyError("The selected tool has no name.")
    if name.lower() == "executesql":
        sql_values = _sql_values(arguments)
        if not sql_values:
            raise ToolSafetyError("ExecuteSql requires a discoverable SQL/query argument before it can be validated.")
        if not allow_nonselect_sql and any(not is_read_only_sql(sql) for sql in sql_values):
            raise ToolSafetyError(
                "ExecuteSql is restricted to one SELECT/WITH statement. Enable non-select SQL only after an explicit security review."
            )
        return
    if not tool_is_read_only(tool) and not allow_mutating:
        raise ToolSafetyError(
            "This tool is not marked or recognized as read-only. The service administrator must enable mutating tools before it can run."
        )


def risk_label(tool: dict[str, Any]) -> str:
    if str(tool.get("name", "")).lower() == "executesql":
        return "SQL — validated at execution"
    return "Read-only" if tool_is_read_only(tool) else "Potentially mutating"
