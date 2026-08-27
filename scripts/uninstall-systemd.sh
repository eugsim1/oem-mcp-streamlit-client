#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PURGE_DATA=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge-data) PURGE_DATA=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ "$(id -u)" -eq 0 ]] || die "run with sudo"
need_command systemctl
systemctl disable --now oem-mcp-streamlit.service 2>/dev/null || true
rm -f -- /etc/systemd/system/oem-mcp-streamlit.service
systemctl daemon-reload
if [[ ${PURGE_DATA} -eq 1 ]]; then
  rm -rf -- /opt/oem-mcp-streamlit /etc/oem-mcp-streamlit /var/lib/oem-mcp-streamlit /var/log/oem-mcp-streamlit
  printf 'Removed service, virtual environment, configuration, data, and logs. This cannot be recovered from this script.\n'
else
  printf 'Removed the service unit only. Configuration, data, logs, and virtual environment were preserved.\n'
fi
