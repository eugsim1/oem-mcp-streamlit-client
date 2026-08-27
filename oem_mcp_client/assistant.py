from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class AssistantPlan:
    tool_name: str
    arguments: dict[str, Any]
    explanation: str
    confidence: float
    provider: str = "local"
    model: str = "deterministic-tool-planner"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9_]+", value.casefold()) if len(term) > 2}


def rank_tools(prompt: str, tools: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    prompt_terms = _terms(prompt)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for tool in tools:
        name = str(tool.get("name", ""))
        searchable = f"{name} {tool.get('title', '')} {tool.get('description', '')}"
        overlap = len(prompt_terms.intersection(_terms(searchable)))
        score = overlap / max(1, len(prompt_terms))
        if name.lower() == "executesql" and any(term in prompt_terms for term in ("sql", "query", "select")):
            score += 0.5
        ranked.append((round(min(score, 1.0), 3), tool))
    return sorted(ranked, key=lambda item: (item[0], str(item[1].get("name", ""))), reverse=True)


def local_plan(prompt: str, tools: list[dict[str, Any]]) -> AssistantPlan:
    if not prompt.strip():
        raise ValueError("A request is required.")
    ranked = rank_tools(prompt, tools)
    if not ranked:
        raise ValueError("No discovered tools are available to plan against.")
    score, tool = ranked[0]
    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    arguments: dict[str, Any] = {}
    json_match = re.search(r"\{.*\}", prompt, flags=re.S)
    if json_match:
        try:
            candidate = json.loads(json_match.group(0))
            if isinstance(candidate, dict):
                arguments = candidate
        except json.JSONDecodeError:
            pass
    if not arguments and str(tool.get("name", "")).lower() == "executesql":
        sql_match = re.search(r"\b(?:select|with)\b.*", prompt, flags=re.I | re.S)
        if sql_match:
            sql_key = next((name for name in properties if re.search(r"sql|query|statement", str(name), re.I)), "sql")
            arguments[sql_key] = sql_match.group(0).strip()
    return AssistantPlan(
        tool_name=str(tool.get("name", "")),
        arguments=arguments,
        explanation=(
            "Deterministic ranking selected the discovered tool with the strongest term overlap. "
            "Review and complete every argument."
        ),
        confidence=score,
        input_tokens=max(1, len(prompt.split())),
        output_tokens=max(1, len(json.dumps(arguments).split())),
    )


class OciGenAiPlanner:
    """Optional OCI Generative AI OpenAI-compatible planner; it never executes tools."""

    def __init__(self, endpoint: str, api_key: str, model: str, timeout_seconds: int = 60) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("OCI Generative AI endpoint must use HTTPS.")
        if not api_key:
            raise ValueError("OCI Generative AI API key is required.")
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = max(5, min(int(timeout_seconds), 300))

    def plan(self, prompt: str, tools: list[dict[str, Any]]) -> AssistantPlan:
        compact_tools = [
            {
                "name": tool.get("name"),
                "description": tool.get("description"),
                "inputSchema": tool.get("inputSchema"),
            }
            for tool in tools[:200]
        ]
        system = (
            "Select exactly one OEM MCP tool. Return JSON only with keys tool_name, arguments, explanation, confidence. "
            "Do not invent a tool or execute anything. Arguments must conform to the supplied schema."
        )
        started = time.monotonic()
        response = requests.post(
            f"{self.endpoint}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps({"request": prompt, "tools": compact_tools})},
                ],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ValueError("OCI Generative AI response contained no choices.")
        content = choices[0].get("message", {}).get("content", "")
        proposal = json.loads(content)
        known = {str(tool.get("name", "")) for tool in tools}
        if proposal.get("tool_name") not in known:
            raise ValueError("OCI Generative AI proposed a tool that was not discovered from OEM.")
        arguments = proposal.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError("OCI Generative AI did not return an arguments object.")
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return AssistantPlan(
            tool_name=str(proposal["tool_name"]),
            arguments=arguments,
            explanation=str(proposal.get("explanation", "Review the generated proposal.")),
            confidence=max(0.0, min(float(proposal.get("confidence", 0)), 1.0)),
            provider="oci-generative-ai",
            model=self.model,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=round((time.monotonic() - started) * 1000),
        )


def estimated_cost(input_tokens: int, output_tokens: int, input_per_million: float, output_per_million: float) -> float:
    return round((max(0, input_tokens) * input_per_million + max(0, output_tokens) * output_per_million) / 1_000_000, 8)
