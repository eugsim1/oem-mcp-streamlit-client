from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any


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


@dataclass(frozen=True)
class AssistantAnswer:
    text: str
    provider: str
    model: str
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
    """OCI Generative AI planner and result explainer; it never executes OEM tools."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 60,
        *,
        auth_mode: str = "api_key",
        project_ocid: str = "",
        profile: str = "DEFAULT",
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("OCI Generative AI endpoint must use HTTPS.")
        normalized_auth = auth_mode.strip().casefold().replace("-", "_")
        if normalized_auth not in {"api_key", "instance_principal", "resource_principal", "session"}:
            raise ValueError(
                "OCI Generative AI authentication mode must be api_key, instance_principal, resource_principal, or session."
            )
        if normalized_auth == "api_key" and not api_key:
            raise ValueError("OCI Generative AI API key is required when OCI_GENAI_AUTH_MODE=api_key.")
        if not model.strip():
            raise ValueError("OCI Generative AI model is required.")
        if "/openai/v1" in endpoint and not project_ocid.strip():
            raise ValueError("OCI_GENAI_PROJECT_OCID is required for the /openai/v1 endpoint.")
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.model = model.strip()
        self.auth_mode = normalized_auth
        self.project_ocid = project_ocid.strip()
        self.profile = profile.strip() or "DEFAULT"
        self.timeout_seconds = max(5, min(int(timeout_seconds), 300))

    def _openai_client(self) -> Any:
        try:
            import httpx
            from openai import OpenAI
        except ImportError as exc:
            raise ValueError("OCI Generative AI support is not installed; rerun the project installer.") from exc

        kwargs: dict[str, Any] = {
            "base_url": self.endpoint,
            "api_key": self.api_key if self.auth_mode == "api_key" else "not-used",
            "timeout": self.timeout_seconds,
        }
        if self.project_ocid:
            kwargs["project"] = self.project_ocid
        if self.auth_mode != "api_key":
            try:
                from oci_genai_auth import (
                    OciInstancePrincipalAuth,
                    OciResourcePrincipalAuth,
                    OciSessionAuth,
                )
            except ImportError as exc:
                raise ValueError("OCI IAM authentication support is not installed; rerun the project installer.") from exc
            if self.auth_mode == "instance_principal":
                auth = OciInstancePrincipalAuth()
            elif self.auth_mode == "resource_principal":
                auth = OciResourcePrincipalAuth()
            else:
                auth = OciSessionAuth(profile_name=self.profile)
            kwargs["http_client"] = httpx.Client(auth=auth, timeout=self.timeout_seconds)
        return OpenAI(**kwargs)

    @staticmethod
    def _message_text(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            raise ValueError("OCI Generative AI response contained no choices.")
        content = getattr(getattr(choices[0], "message", None), "content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                text = getattr(item, "text", None)
                if text is None and isinstance(item, dict):
                    text = item.get("text")
                if text:
                    parts.append(str(text))
            return "\n".join(parts).strip()
        return str(content or "").strip()

    @staticmethod
    def _usage(response: Any) -> tuple[int, int]:
        usage = getattr(response, "usage", None)
        return (
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
        )

    def _complete(self, messages: list[dict[str, str]]) -> tuple[str, int, int, int]:
        started = time.monotonic()
        client = self._openai_client()
        try:
            response = client.chat.completions.create(
                model=self.model,
                temperature=0,
                messages=messages,
            )
            text = self._message_text(response)
            input_tokens, output_tokens = self._usage(response)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"OCI Generative AI request failed: {type(exc).__name__}: {exc}") from exc
        finally:
            client.close()
        if not text:
            raise ValueError("OCI Generative AI returned an empty response.")
        return text, input_tokens, output_tokens, round((time.monotonic() - started) * 1000)

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        clean = content.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.I | re.S).strip()
        try:
            value = json.loads(clean)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", clean, flags=re.S)
            if not match:
                raise ValueError("OCI Generative AI did not return a JSON planning object.") from None
            try:
                value = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise ValueError("OCI Generative AI returned invalid JSON for the tool proposal.") from exc
        if not isinstance(value, dict):
            raise ValueError("OCI Generative AI planning response must be a JSON object.")
        return value

    def plan(self, prompt: str, tools: list[dict[str, Any]]) -> AssistantPlan:
        if not prompt.strip():
            raise ValueError("An operational request is required.")
        if not tools:
            raise ValueError("No discovered OEM tools are available for the selected strategy.")
        compact_tools = [
            {
                "name": tool.get("name"),
                "description": tool.get("description"),
                "inputSchema": tool.get("inputSchema"),
            }
            for tool in tools[:200]
        ]
        system = (
            "You are a cautious Oracle Enterprise Manager tool planner. Select exactly one supplied OEM MCP tool and do not execute it. "
            "Prefer a purpose-built incident, target, job, status, or metric operation over ExecuteSql. Use ExecuteSql only when it is "
            "the only supplied tool or the request explicitly requires SQL. For ExecuteSql, generate exactly one read-only SELECT or WITH "
            "statement and never generate DDL, DML, PL/SQL, or multiple statements. Do not invent tool names, arguments, target names, "
            "database objects, or filters. Return JSON only with keys tool_name, arguments, explanation, confidence. Arguments must "
            "conform to the supplied input schema. If required information is missing, return an empty arguments object and explain "
            "what is missing."
        )
        content, input_tokens, output_tokens, latency_ms = self._complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({"request": prompt, "tools": compact_tools})},
            ]
        )
        proposal = self._json_object(content)
        known = {str(tool.get("name", "")) for tool in tools}
        if proposal.get("tool_name") not in known:
            raise ValueError("OCI Generative AI proposed a tool that was not discovered from OEM.")
        arguments = proposal.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError("OCI Generative AI did not return an arguments object.")
        return AssistantPlan(
            tool_name=str(proposal["tool_name"]),
            arguments=arguments,
            explanation=str(proposal.get("explanation", "Review the generated proposal.")),
            confidence=max(0.0, min(float(proposal.get("confidence", 0)), 1.0)),
            provider="oci-generative-ai",
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )

    def answer(self, prompt: str, plan: AssistantPlan, result: Any) -> AssistantAnswer:
        max_chars = 120_000
        serialized = json.dumps(result, ensure_ascii=False, default=str)
        if len(serialized) > max_chars:
            serialized = serialized[:max_chars] + "\n[RESULT TRUNCATED BY CLIENT]"
        system = (
            "Answer the operator's question using only the supplied Oracle Enterprise Manager MCP result. Do not invent targets, "
            "incidents, jobs, metrics, causes, or remediation. Clearly say when the result is empty, truncated, ambiguous, or contains "
            "an OEM error. Provide a concise operational summary followed by the most relevant returned facts. Do not claim that an "
            "action was performed."
        )
        content, input_tokens, output_tokens, latency_ms = self._complete(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": prompt,
                            "executed_tool": plan.tool_name,
                            "reviewed_arguments": plan.arguments,
                            "oem_result": serialized,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        return AssistantAnswer(
            text=content,
            provider="oci-generative-ai",
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )


def estimated_cost(input_tokens: int, output_tokens: int, input_per_million: float, output_per_million: float) -> float:
    return round((max(0, input_tokens) * input_per_million + max(0, output_tokens) * output_per_million) / 1_000_000, 8)
