from __future__ import annotations

import argparse
import json
import os
import platform
import sys

from .client import OemMcpClient
from .config import SUPPORTED_PROTOCOLS, ConnectionConfig, runtime_paths, safe_endpoint
from .metrics import collect_local_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Redacted diagnostics for the OEM MCP Streamlit client")
    parser.add_argument("--connect", action="store_true", help="Initialize the configured OEM MCP endpoint and list capabilities")
    args = parser.parse_args()
    data_dir, log_dir, profile_file = runtime_paths()
    report = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "data_dir": str(data_dir),
        "log_dir": str(log_dir),
        "profile_file": str(profile_file),
        "supported_protocols": list(SUPPORTED_PROTOCOLS),
        "local_metrics": collect_local_metrics(),
    }
    print(json.dumps(report, indent=2, default=str))
    if not args.connect:
        return 0
    endpoint = os.getenv("OEM_MCP_ENDPOINT", "")
    username = os.getenv("OEM_MCP_USERNAME", "")
    password = os.getenv("OEM_MCP_PASSWORD", "")
    config = ConnectionConfig(
        endpoint=endpoint,
        username=username,
        protocol_version=os.getenv("OEM_MCP_PROTOCOL_VERSION", SUPPORTED_PROTOCOLS[0]),
        verify_tls=os.getenv("OEM_MCP_VERIFY_TLS", "true").lower() not in {"0", "false", "no"},
        ca_bundle=os.getenv("OEM_MCP_CA_BUNDLE", ""),
        timeout_seconds=int(os.getenv("OEM_MCP_TIMEOUT_SECONDS", "60")),
    )
    print(f"Connecting to {safe_endpoint(endpoint)} as a redacted user fingerprint...")
    with OemMcpClient(config, password, allow_http=os.getenv("OEM_MCP_ALLOW_HTTP", "false").lower() == "true") as client:
        client.initialize()
        discovery = client.discover_all()
        print(
            json.dumps(
                {
                    "endpoint": safe_endpoint(endpoint),
                    "protocol": discovery["protocolVersion"],
                    "server_info": discovery["serverInfo"],
                    "tool_count": len(discovery["tools"]),
                    "prompt_count": len(discovery["prompts"]),
                    "resource_count": len(discovery["resources"]),
                    "resource_template_count": len(discovery["resourceTemplates"]),
                    "optional_list_errors": discovery["errors"],
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
