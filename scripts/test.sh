#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

VENV_DIR="${PROJECT_ROOT}/.venv"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --venv) VENV_DIR="$2"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -x "${VENV_DIR}/bin/python" ]] || die "virtual environment is missing: ${VENV_DIR}"
cd "${PROJECT_ROOT}"
"${VENV_DIR}/bin/python" -m compileall -q app.py oem_mcp_client tests
"${VENV_DIR}/bin/python" -m ruff check .
"${VENV_DIR}/bin/python" -m pytest -q --cov=oem_mcp_client --cov-report=term-missing
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck scripts/*.sh
else
  printf 'WARNING: shellcheck is not installed; shell syntax was not linted.\n' >&2
fi
