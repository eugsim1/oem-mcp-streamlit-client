#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PORT=8501
RUN_DIR="${PROJECT_ROOT}/.run"
WAIT_SECONDS=30
FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    --wait-seconds) WAIT_SECONDS="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
validate_port "${PORT}"
PID_FILE="${RUN_DIR}/streamlit-standalone-${PORT}.pid"
[[ -r "${PID_FILE}" ]] || die "PID file does not exist: ${PID_FILE}"
read -r PID <"${PID_FILE}"
[[ "${PID}" =~ ^[0-9]+$ ]] || die "invalid PID file: ${PID_FILE}"
if ! kill -0 "${PID}" 2>/dev/null; then
  rm -f -- "${PID_FILE}"
  printf 'Removed stale PID file for process %s.\n' "${PID}"
  exit 0
fi
OWNER_UID="$(ps -o uid= -p "${PID}" | tr -d '[:space:]')"
[[ "${OWNER_UID}" == "$(id -u)" ]] || die "PID ${PID} belongs to another user"
COMMAND_LINE="$(tr '\0' ' ' <"/proc/${PID}/cmdline")"
[[ "${COMMAND_LINE}" == *streamlit* && "${COMMAND_LINE}" == *"${PROJECT_ROOT}/app.py"* ]] || die "PID ${PID} is not this project's Streamlit process"
kill -TERM "${PID}"
for (( elapsed=0; elapsed<WAIT_SECONDS; elapsed++ )); do
  if ! kill -0 "${PID}" 2>/dev/null; then
    rm -f -- "${PID_FILE}"
    printf 'Standalone Streamlit stopped (PID %s, port %s).\n' "${PID}" "${PORT}"
    exit 0
  fi
  sleep 1
done
if [[ ${FORCE} -eq 1 ]]; then
  kill -KILL "${PID}"
  rm -f -- "${PID_FILE}"
  printf 'Standalone Streamlit force-stopped (PID %s).\n' "${PID}"
  exit 0
fi
die "PID ${PID} did not stop within ${WAIT_SECONDS} seconds; rerun with --force if appropriate"
