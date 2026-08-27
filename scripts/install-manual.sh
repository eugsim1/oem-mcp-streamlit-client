#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PYTHON_REQUESTED=""
VENV_DIR="${PROJECT_ROOT}/.venv"
ENV_FILE="${PROJECT_ROOT}/.runtime/oem-mcp-streamlit.env"
INSTALL_DEV=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python-bin) PYTHON_REQUESTED="$2"; shift 2 ;;
    --venv) VENV_DIR="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --with-dev) INSTALL_DEV=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

for command_name in chmod mkdir; do need_command "${command_name}"; done
PYTHON_BIN="$(resolve_python "${PYTHON_REQUESTED}")"
mkdir -p -- "$(dirname -- "${ENV_FILE}")" "${PROJECT_ROOT}/data" "${PROJECT_ROOT}/logs" "${PROJECT_ROOT}/.run"
if [[ ! -e "${ENV_FILE}" ]]; then
  install -m 0600 "${PROJECT_ROOT}/config/oem-mcp-streamlit.env.example" "${ENV_FILE}"
  printf 'Created runtime environment file: %s\n' "${ENV_FILE}"
fi
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi
"${VENV_DIR}/bin/python" -c 'import sys; raise SystemExit(0 if (3, 9) <= sys.version_info[:2] < (3, 14) else 1)' || \
  die "existing virtual environment does not use Python 3.9 through 3.13: ${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
if [[ ${INSTALL_DEV} -eq 1 ]]; then
  "${VENV_DIR}/bin/python" -m pip install -r "${PROJECT_ROOT}/requirements-dev.txt"
else
  "${VENV_DIR}/bin/python" -m pip install -r "${PROJECT_ROOT}/requirements.txt"
fi
"${VENV_DIR}/bin/python" -m compileall -q "${PROJECT_ROOT}/oem_mcp_client" "${PROJECT_ROOT}/app.py"
printf 'Manual installation complete.\n'
printf 'Environment : %s\n' "${ENV_FILE}"
printf 'Start       : scripts/start-standalone.sh --env-file %s --port 8501\n' "${ENV_FILE}"
printf 'Stop        : scripts/stop-standalone.sh --port 8501\n'
