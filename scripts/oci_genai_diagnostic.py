#!/usr/bin/env python3
"""Test OCI Generative AI API-key authentication without Streamlit.

The program intentionally uses only the Python standard library. It validates
the endpoint before opening a connection, never prints the API-key secret, and
labels HTML responses as transport/proxy errors rather than OCI IAM responses.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

API_KEY_PATH = "/20231130/actions/v1"
PROJECT_PATH = "/openai/v1"
PROXY_VARIABLES = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)


class ConfigurationError(ValueError):
    """Raised when the diagnostic configuration is unsafe or incomplete."""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep the bearer secret on the validated OCI hostname."""

    def redirect_request(self, request: object, file_pointer: object, code: int, message: str, headers: object, new_url: str) -> None:
        return None


@dataclass(frozen=True)
class DiagnosticConfig:
    endpoint: str
    model: str
    auth_mode: str
    project_ocid: str
    api_key: str
    timeout_seconds: int


@dataclass(frozen=True)
class HttpResult:
    status: int
    content_type: str
    body: str
    request_id: str


def load_env_file(path: Path) -> dict[str, str]:
    """Read a simple KEY=VALUE file without executing it as shell code."""
    if not path.is_file():
        raise ConfigurationError(f"environment file is not readable: {path}")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigurationError(f"invalid environment assignment at {path}:{line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not name.replace("_", "A").isalnum() or not (name[0].isalpha() or name[0] == "_"):
            raise ConfigurationError(f"invalid environment variable name at {path}:{line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


def _setting(name: str, explicit: object, file_values: Mapping[str, str], environ: Mapping[str, str], default: str = "") -> str:
    if explicit is not None:
        return str(explicit)
    if name in file_values:
        return file_values[name]
    return environ.get(name, default)


def resolve_config(args: argparse.Namespace, environ: Mapping[str, str]) -> DiagnosticConfig:
    file_values: dict[str, str] = {}
    if args.env_file:
        file_values = load_env_file(Path(args.env_file).expanduser())

    timeout_text = _setting(
        "OCI_GENAI_TIMEOUT_SECONDS",
        args.timeout_seconds,
        file_values,
        environ,
        "120",
    )
    try:
        timeout_seconds = int(timeout_text)
    except ValueError as exc:
        raise ConfigurationError("OCI_GENAI_TIMEOUT_SECONDS must be an integer") from exc

    return DiagnosticConfig(
        endpoint=_setting("OCI_GENAI_OPENAI_ENDPOINT", args.endpoint, file_values, environ),
        model=_setting("OCI_GENAI_MODEL", args.model, file_values, environ),
        auth_mode=_setting("OCI_GENAI_AUTH_MODE", None, file_values, environ, "api_key").lower(),
        project_ocid=_setting("OCI_GENAI_PROJECT_OCID", args.project_ocid, file_values, environ),
        api_key=_setting("OCI_GENAI_API_KEY", None, file_values, environ),
        timeout_seconds=timeout_seconds,
    )


def validate_config(config: DiagnosticConfig, *, require_secret: bool = True) -> DiagnosticConfig:
    endpoint = config.endpoint.strip().rstrip("/")
    if not endpoint:
        raise ConfigurationError("OCI_GENAI_OPENAI_ENDPOINT is required")
    if any(character in endpoint for character in "[]()"):
        raise ConfigurationError(
            "OCI_GENAI_OPENAI_ENDPOINT contains Markdown formatting; use only the plain https:// URL"
        )

    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ConfigurationError("OCI_GENAI_OPENAI_ENDPOINT must be a valid https:// URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError("OCI_GENAI_OPENAI_ENDPOINT must not contain credentials, a query, or a fragment")
    if not (
        parsed.hostname.startswith("inference.generativeai.")
        and parsed.hostname.endswith(".oci.oraclecloud.com")
    ):
        raise ConfigurationError("OCI_GENAI_OPENAI_ENDPOINT must use an OCI Generative AI inference hostname")
    if parsed.port not in {None, 443}:
        raise ConfigurationError("OCI_GENAI_OPENAI_ENDPOINT must use the default HTTPS port 443")

    path = parsed.path.rstrip("/")
    if path not in {API_KEY_PATH, PROJECT_PATH}:
        raise ConfigurationError(
            f"unsupported endpoint path {path or '/'}; use {API_KEY_PATH} for an API key or {PROJECT_PATH} for a project endpoint"
        )
    if not config.model.strip():
        raise ConfigurationError("OCI_GENAI_MODEL is required")
    if config.auth_mode != "api_key":
        raise ConfigurationError(
            "this standalone diagnostic tests OCI Generative AI API-key secrets only; set OCI_GENAI_AUTH_MODE=api_key"
        )
    if config.timeout_seconds < 1 or config.timeout_seconds > 600:
        raise ConfigurationError("OCI_GENAI_TIMEOUT_SECONDS must be between 1 and 600")

    project_ocid = config.project_ocid.strip()
    if path == PROJECT_PATH and not project_ocid.startswith("ocid1.generativeaiproject."):
        raise ConfigurationError(
            "the /openai/v1 endpoint requires OCI_GENAI_PROJECT_OCID beginning with ocid1.generativeaiproject."
        )

    api_key = config.api_key.strip()
    if require_secret and not api_key:
        raise ConfigurationError("OCI_GENAI_API_KEY is empty")
    if api_key.startswith("ocid1.generativeaiapikey."):
        raise ConfigurationError(
            "OCI_GENAI_API_KEY contains the API-key OCID; it must contain the one-time generated secret instead"
        )

    return replace(
        config,
        endpoint=endpoint,
        model=config.model.strip(),
        project_ocid=project_ocid,
        api_key=api_key,
    )


def uses_project_endpoint(config: DiagnosticConfig) -> bool:
    return urllib.parse.urlsplit(config.endpoint).path.rstrip("/") == PROJECT_PATH


def build_request(config: DiagnosticConfig) -> urllib.request.Request:
    payload = json.dumps(
        {
            "model": config.model,
            "input": "Reply with exactly OCI_GENAI_OK",
        }
    ).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "User-Agent": "oem-mcp-streamlit-client-diagnostic/1.1.2",
    }
    if uses_project_endpoint(config):
        headers["OpenAI-Project"] = config.project_ocid
    # The base URL is restricted to HTTPS by validate_config.
    return urllib.request.Request(  # noqa: S310
        f"{config.endpoint}/responses",
        data=payload,
        headers=headers,
        method="POST",
    )


def _decode_body(raw: bytes) -> str:
    return raw[:16384].decode("utf-8", errors="replace")


def send_request(config: DiagnosticConfig, *, no_proxy: bool = False) -> HttpResult:
    config = validate_config(config)
    request = build_request(config)
    proxy_handler = urllib.request.ProxyHandler({}) if no_proxy else urllib.request.ProxyHandler()
    opener = urllib.request.build_opener(proxy_handler, NoRedirectHandler())
    try:
        # The URL was restricted to HTTPS and validated before this call.
        with opener.open(request, timeout=config.timeout_seconds) as response:  # noqa: S310
            return HttpResult(
                status=response.status,
                content_type=response.headers.get("Content-Type", ""),
                body=_decode_body(response.read()),
                request_id=response.headers.get("opc-request-id", ""),
            )
    except urllib.error.HTTPError as exc:
        return HttpResult(
            status=exc.code,
            content_type=exc.headers.get("Content-Type", "") if exc.headers else "",
            body=_decode_body(exc.read()),
            request_id=exc.headers.get("opc-request-id", "") if exc.headers else "",
        )


def is_html_response(result: HttpResult) -> bool:
    return "text/html" in result.content_type.lower() or result.body.lstrip().lower().startswith(("<!doctype html", "<html"))


def _extract_output_text(body: str) -> str:
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        return ""
    if isinstance(document, dict) and isinstance(document.get("output_text"), str):
        return document["output_text"]
    if not isinstance(document, dict) or not isinstance(document.get("output"), list):
        return ""
    for item in document["output"]:
        if not isinstance(item, dict) or not isinstance(item.get("content"), list):
            continue
        for content in item["content"]:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                return content["text"]
    return ""


def print_safe_configuration(config: DiagnosticConfig, environ: Mapping[str, str], *, no_proxy: bool) -> None:
    proxy_names = [name for name in PROXY_VARIABLES if environ.get(name)]
    route = "project OpenAI-compatible endpoint" if uses_project_endpoint(config) else "regional API-key endpoint"
    print("Safe configuration summary")
    print(f"  Endpoint: {config.endpoint}")
    print(f"  Route: {route}")
    print(f"  Model: {config.model}")
    print(f"  Authentication: {config.auth_mode}")
    print(f"  Project OCID configured: {'yes' if config.project_ocid else 'no'}")
    print(f"  API-key secret configured: {'yes' if config.api_key else 'no'}; length={len(config.api_key)}")
    print(f"  Proxy handling: {'disabled for this request' if no_proxy else 'environment/default'}")
    print(f"  Proxy variables present: {', '.join(proxy_names) if proxy_names else 'none'} (values hidden)")
    if not uses_project_endpoint(config) and config.project_ocid:
        print(f"  Note: OCI_GENAI_PROJECT_OCID is not sent with the {API_KEY_PATH} endpoint")


def explain_result(result: HttpResult, *, no_proxy: bool) -> int:
    print(f"HTTP status: {result.status}")
    print(f"Content-Type: {result.content_type or '(missing)'}")
    if result.request_id:
        print(f"OCI request ID: {result.request_id}")

    if is_html_response(result):
        print(
            "ERROR: an HTML page answered the API request. OCI Generative AI authentication errors are JSON; "
            "this normally indicates a proxy/gateway response or a malformed/re-written URL."
        )
        if no_proxy:
            print("The direct request also returned HTML; check DNS, egress routing, TLS interception, and the endpoint URL.")
        else:
            print("Repeat the diagnostic with --no-proxy. If that works, correct HTTPS_PROXY/NO_PROXY with the network team.")
        return 3

    try:
        document = json.loads(result.body) if result.body else {}
    except json.JSONDecodeError:
        document = {}

    if 200 <= result.status < 300:
        output_text = _extract_output_text(result.body)
        print("SUCCESS: OCI Generative AI accepted the API-key request.")
        if output_text:
            print(f"Model output: {output_text[:500]}")
        return 0

    message = document.get("message", "") if isinstance(document, dict) else ""
    code = document.get("code", "") if isinstance(document, dict) else ""
    if code or message:
        print(f"OCI error: code={code or '(missing)'} message={message or '(missing)'}")
    else:
        print(f"Response body (truncated): {result.body[:1000] or '(empty)'}")

    if result.status == 400:
        print("Check the model name, request route, and required project header for /openai/v1.")
    elif result.status == 401:
        print("Check that OCI_GENAI_API_KEY is the generated secret, not the API-key OCID, and that it is active.")
    elif result.status in {403, 404}:
        print("Check the API-key IAM policy, key region, model availability, and—on /openai/v1—the project OCID/access.")
    else:
        print("Use the OCI request ID with OCI Logging/support, and check service health and regional egress.")
    return 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely test OCI Generative AI API-key authentication without Streamlit or curl quoting.",
    )
    parser.add_argument("--env-file", help="read configuration from a protected KEY=VALUE file")
    parser.add_argument("--endpoint", help="override OCI_GENAI_OPENAI_ENDPOINT")
    parser.add_argument("--model", help="override OCI_GENAI_MODEL")
    parser.add_argument("--project-ocid", help="override OCI_GENAI_PROJECT_OCID")
    parser.add_argument("--timeout-seconds", type=int, help="override OCI_GENAI_TIMEOUT_SECONDS")
    parser.add_argument("--no-proxy", action="store_true", help="bypass HTTP(S) proxy variables for this request")
    parser.add_argument("--no-prompt", action="store_true", help="fail instead of securely prompting for a missing secret")
    parser.add_argument("--check-config", action="store_true", help="validate and print safe configuration without sending a request")
    return parser


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    active_environ = os.environ if environ is None else environ
    try:
        config = resolve_config(args, active_environ)
        config = validate_config(config, require_secret=False)
        if not config.api_key and not args.check_config and not args.no_prompt:
            config = replace(config, api_key=getpass.getpass("OCI Generative AI API-key secret: ").strip())
        config = validate_config(config, require_secret=not args.check_config)
    except ConfigurationError as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return 2

    print_safe_configuration(config, active_environ, no_proxy=args.no_proxy)
    if args.check_config:
        print("Configuration validation completed; no network request was sent.")
        return 0

    try:
        result = send_request(config, no_proxy=args.no_proxy)
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        print(f"CONNECTION ERROR: {exc}", file=sys.stderr)
        if not args.no_proxy:
            print("Retry with --no-proxy to distinguish proxy configuration from direct egress.", file=sys.stderr)
        return 4
    return explain_result(result, no_proxy=args.no_proxy)


if __name__ == "__main__":
    raise SystemExit(main())
