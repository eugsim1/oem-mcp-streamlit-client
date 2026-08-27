from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .safety import tool_is_read_only


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    reason: str
    matched_rule: str = ""


class PolicyEngine:
    """Ordered, deny-first tool and target policy rules."""

    def __init__(self, rules: list[dict[str, Any]] | None = None) -> None:
        self.rules = list(rules or [])

    @classmethod
    def from_file(cls, path: str | Path | None) -> PolicyEngine:
        if not path:
            return cls()
        source = Path(path)
        if not source.is_file():
            raise ValueError(f"Policy file does not exist: {source}")
        data = json.loads(source.read_text(encoding="utf-8"))
        rules = data.get("rules") if isinstance(data, dict) else None
        if not isinstance(rules, list):
            raise ValueError("Policy file must contain a rules array.")
        return cls([rule for rule in rules if isinstance(rule, dict)])

    @staticmethod
    def _targets(arguments: dict[str, Any]) -> list[str]:
        values: list[str] = []
        for key, value in arguments.items():
            if any(word in str(key).lower() for word in ("target", "host", "database", "instance", "entity")):
                if isinstance(value, list):
                    values.extend(str(item) for item in value)
                elif value not in (None, ""):
                    values.append(str(value))
        return values or ["*"]

    def evaluate(self, role: str, tool: dict[str, Any], arguments: dict[str, Any]) -> PolicyDecision:
        name = str(tool.get("name", ""))
        targets = self._targets(arguments)
        matches: list[dict[str, Any]] = []
        for rule in self.rules:
            roles = rule.get("roles", ["*"])
            tools = rule.get("tools", ["*"])
            target_patterns = rule.get("targets", ["*"])
            if not isinstance(roles, list) or not isinstance(tools, list) or not isinstance(target_patterns, list):
                continue
            if not any(fnmatch.fnmatchcase(role.casefold(), str(pattern).casefold()) for pattern in roles):
                continue
            if not any(fnmatch.fnmatchcase(name.casefold(), str(pattern).casefold()) for pattern in tools):
                continue
            if not all(
                any(fnmatch.fnmatchcase(target.casefold(), str(pattern).casefold()) for pattern in target_patterns)
                for target in targets
            ):
                continue
            matches.append(rule)

        denied = next((rule for rule in matches if str(rule.get("effect", "deny")).lower() == "deny"), None)
        if denied:
            return PolicyDecision(False, False, str(denied.get("reason") or "Denied by policy."), str(denied.get("name") or "deny"))
        allowed = next((rule for rule in matches if str(rule.get("effect", "")).lower() == "allow"), None)
        if allowed:
            require_approval = bool(allowed.get("requireApproval", False))
            return PolicyDecision(
                True,
                require_approval,
                str(allowed.get("reason") or "Allowed by policy."),
                str(allowed.get("name") or "allow"),
            )
        if tool_is_read_only(tool) or name.casefold() == "executesql":
            return PolicyDecision(True, False, "Allowed by the built-in read-only default.", "built-in-read-only")
        return PolicyDecision(False, True, "No policy rule permits this potentially mutating tool.", "built-in-deny")
