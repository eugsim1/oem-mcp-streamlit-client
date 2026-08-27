#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ENV_FILE="${PROJECT_ROOT}/.runtime/oem-mcp-streamlit.env"
VENV_DIR="${PROJECT_ROOT}/.venv"
CONNECT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --venv) VENV_DIR="$2"; shift 2 ;;
    --connect) CONNECT=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
need_command uname
load_env_file "${ENV_FILE}"
[[ -x "${VENV_DIR}/bin/python" ]] || die "virtual environment is missing: ${VENV_DIR}"
printf 'Project       : %s\n' "${PROJECT_ROOT}"
printf 'Kernel        : %s\n' "$(uname -srmo)"
printf 'Python        : %s\n' "$("${VENV_DIR}/bin/python" --version 2>&1)"
printf 'Endpoint set  : %s\n' "$([[ -n "${OEM_MCP_ENDPOINT:-}" ]] && printf yes || printf no)"
printf 'Username set  : %s\n' "$([[ -n "${OEM_MCP_USERNAME:-}" ]] && printf yes || printf no)"
printf 'Password set  : %s\n' "$([[ -n "${OEM_MCP_PASSWORD:-}" ]] && printf yes || printf no)"
printf 'TLS verify    : %s\n' "${OEM_MCP_VERIFY_TLS:-true}"
ARGS=()
if [[ ${CONNECT} -eq 1 ]]; then ARGS+=(--connect); fi
cd "${PROJECT_ROOT}"
"${VENV_DIR}/bin/python" -m oem_mcp_client.diagnostics "${ARGS[@]}"
