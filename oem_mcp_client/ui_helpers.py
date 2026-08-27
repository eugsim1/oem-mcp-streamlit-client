from __future__ import annotations

import json
from typing import Any

import jsonschema
import pandas as pd
import streamlit as st


def _json_default(value: Any, expected_type: str) -> str:
    if value is not None:
        return json.dumps(value, indent=2)
    return "[]" if expected_type == "array" else "{}"


def schema_arguments(schema: dict[str, Any], *, key_prefix: str) -> tuple[dict[str, Any], list[str]]:
    properties = schema.get("properties") if isinstance(schema, dict) else {}
    properties = properties if isinstance(properties, dict) else {}
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    arguments: dict[str, Any] = {}
    errors: list[str] = []
    if not properties:
        raw = st.text_area("Arguments (JSON object)", value="{}", height=180, key=f"{key_prefix}-raw")
        try:
            parsed = json.loads(raw or "{}")
            if not isinstance(parsed, dict):
                errors.append("Arguments must be a JSON object.")
            else:
                arguments = parsed
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid arguments JSON: {exc.msg}")
        return arguments, errors

    for name, definition in properties.items():
        definition = definition if isinstance(definition, dict) else {}
        label = f"{name}{' *' if name in required else ''}"
        description = str(definition.get("description") or "")
        value_type = str(definition.get("type") or "string")
        default = definition.get("default")
        enum = definition.get("enum")
        key = f"{key_prefix}-{name}"
        if isinstance(enum, list) and enum:
            value = st.selectbox(label, enum, index=enum.index(default) if default in enum else 0, help=description, key=key)
        elif value_type == "boolean":
            value = st.checkbox(label, value=bool(default), help=description, key=key)
        elif value_type == "integer":
            value = int(st.number_input(label, value=int(default or 0), step=1, help=description, key=key))
        elif value_type == "number":
            value = float(st.number_input(label, value=float(default or 0.0), help=description, key=key))
        elif value_type in {"object", "array"}:
            raw = st.text_area(label, value=_json_default(default, value_type), height=140, help=description, key=key)
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"{name}: invalid JSON ({exc.msg})")
                continue
        else:
            value = st.text_input(label, value="" if default is None else str(default), help=description, key=key)
        if name in required and (value is None or value == ""):
            errors.append(f"{name} is required.")
        elif name in required or value not in (None, ""):
            arguments[name] = value
    if not errors:
        try:
            jsonschema.validate(instance=arguments, schema=schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"Arguments do not match the tool schema: {exc.message}")
    return arguments, errors


def tool_table(tools: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "name": tool.get("name", ""),
                "title": tool.get("title", ""),
                "description": tool.get("description", ""),
                "has_output_schema": bool(tool.get("outputSchema")),
            }
            for tool in tools
        ]
    )


def _tabular_candidate(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        return value
    if isinstance(value, dict):
        for item in value.values():
            candidate = _tabular_candidate(item)
            if candidate:
                return candidate
    return None


def render_tool_result(result: dict[str, Any]) -> None:
    structured = result.get("structuredContent")
    rows = _tabular_candidate(structured)
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    elif structured is not None:
        st.json(structured)
    content = result.get("content") or []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            st.code(str(block.get("text", "")), language=None)
        elif block_type in {"image", "audio"}:
            st.info(
                f"The result contains a {block_type} block. Binary rendering is intentionally disabled; "
                "inspect the raw response if authorized."
            )
        else:
            st.json(block)
    with st.expander("Raw MCP result"):
        st.json(result)
