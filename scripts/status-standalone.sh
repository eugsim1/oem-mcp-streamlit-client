#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PORT=8501
RUN_DIR="${PROJECT_ROOT}/.run"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done
validate_port "${PORT}"
PID_FILE="${RUN_DIR}/streamlit-standalone-${PORT}.pid"
if [[ ! -r "${PID_FILE}" ]]; then
  printf 'Stopped: no PID file at %s\n' "${PID_FILE}"
  exit 3
fi
read -r PID <"${PID_FILE}"
if [[ "${PID}" =~ ^[0-9]+$ ]] && kill -0 "${PID}" 2>/dev/null; then
  printf 'Running: PID %s on configured port %s\n' "${PID}" "${PORT}"
  ps -o pid,etime,cmd -p "${PID}"
  exit 0
fi
printf 'Stale PID file: %s\n' "${PID_FILE}"
exit 1
