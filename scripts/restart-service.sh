#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ "$(id -u)" -eq 0 ]] || die "run with sudo"
need_command systemctl
systemctl restart oem-mcp-streamlit.service
systemctl --no-pager --full status oem-mcp-streamlit.service
