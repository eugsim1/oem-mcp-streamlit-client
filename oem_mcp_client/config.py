from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18")
DEFAULT_ENDPOINT_PATH = "/em/api/mcp"


class ConfigurationError(ValueError):
    """Raised when a connection profile is incomplete or unsafe."""


def normalized_endpoint(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ConfigurationError("The OEM MCP endpoint is required.")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"https", "http"}:
        raise ConfigurationError("The endpoint must use https:// (or http:// only when explicitly enabled).")
    if not parsed.hostname:
        raise ConfigurationError("The endpoint must include a hostname.")
    if parsed.username or parsed.password:
        raise ConfigurationError("Do not place credentials in the endpoint URL.")
    path = parsed.path.rstrip("/") or DEFAULT_ENDPOINT_PATH
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def safe_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or "unknown-host"
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path or DEFAULT_ENDPOINT_PATH
        return urlunsplit((parsed.scheme or "https", f"{host}{port}", path, "", ""))
    except (TypeError, ValueError):
        return "invalid-endpoint"


@dataclass(frozen=True)
class ConnectionConfig:
    endpoint: str
    username: str
    protocol_version: str = SUPPORTED_PROTOCOLS[0]
    verify_tls: bool = True
    ca_bundle: str = ""
    timeout_seconds: int = 60

    def validated(self, allow_http: bool = False) -> ConnectionConfig:
        endpoint = normalized_endpoint(self.endpoint)
        if not allow_http and not endpoint.startswith("https://"):
            raise ConfigurationError("HTTP is disabled. Use an HTTPS OEM console endpoint.")
        username = self.username.strip()
        if not username:
            raise ConfigurationError("The Enterprise Manager username is required.")
        if self.protocol_version not in SUPPORTED_PROTOCOLS:
            raise ConfigurationError(f"Unsupported MCP protocol version: {self.protocol_version}")
        timeout = int(self.timeout_seconds)
        if timeout < 5 or timeout > 600:
            raise ConfigurationError("Timeout must be between 5 and 600 seconds.")
        ca_bundle = self.ca_bundle.strip()
        if ca_bundle and not Path(ca_bundle).is_file():
            raise ConfigurationError(f"CA bundle does not exist: {ca_bundle}")
        return ConnectionConfig(
            endpoint=endpoint,
            username=username,
            protocol_version=self.protocol_version,
            verify_tls=bool(self.verify_tls),
            ca_bundle=ca_bundle,
            timeout_seconds=timeout,
        )

    @property
    def requests_verify(self) -> bool | str:
        if not self.verify_tls:
            return False
        return self.ca_bundle or True

    def as_profile(self) -> dict[str, Any]:
        profile = asdict(self)
        profile["endpoint"] = safe_endpoint(self.endpoint)
        return profile


class ProfileStore:
    """Stores non-secret profiles. Passwords and authorization headers are forbidden."""

    FORBIDDEN_KEYS = {"password", "authorization", "token", "secret", "api_key", "apikey"}

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ConfigurationError("Profile file must contain a JSON object.")
        return {str(name): dict(profile) for name, profile in data.items() if isinstance(profile, dict)}

    def save(self, name: str, config: ConnectionConfig) -> None:
        clean_name = name.strip()
        if not clean_name:
            raise ConfigurationError("Profile name is required.")
        profiles = self.load()
        profile = config.as_profile()
        if self.FORBIDDEN_KEYS.intersection(key.lower() for key in profile):
            raise ConfigurationError("Secret values cannot be stored in a profile.")
        profiles[clean_name] = profile
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="profiles-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(profiles, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def runtime_paths() -> tuple[Path, Path, Path]:
    data_dir = Path(os.getenv("OEM_MCP_DATA_DIR", "./data")).expanduser().resolve()
    log_dir = Path(os.getenv("OEM_MCP_LOG_DIR", "./logs")).expanduser().resolve()
    profile_file = Path(os.getenv("OEM_MCP_PROFILE_FILE", str(data_dir / "profiles.json"))).expanduser().resolve()
    return data_dir, log_dir, profile_file
