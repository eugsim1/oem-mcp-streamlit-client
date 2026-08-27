#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ARGS=("$@")
ENV_FILE="${PROJECT_ROOT}/.runtime/oem-mcp-streamlit.env"
for (( index=0; index<${#ARGS[@]}; index++ )); do
  if [[ "${ARGS[index]}" == "--env-file" ]]; then
    (( index + 1 < ${#ARGS[@]} )) || die "--env-file requires a value"
    ENV_FILE="${ARGS[index + 1]}"
  fi
done
load_env_file "${ENV_FILE}"

PORT="${STREAMLIT_PORT:-8501}"
ADDRESS="${STREAMLIT_ADDRESS:-127.0.0.1}"
VENV_DIR="${PROJECT_ROOT}/.venv"
RUN_DIR="${PROJECT_ROOT}/.run"
LOG_DIR="${OEM_MCP_LOG_DIR:-${PROJECT_ROOT}/logs}"
WAIT_SECONDS=60
ALLOW_NON_LOOPBACK=0
REFRESH_DEPENDENCIES=0

set -- "${ARGS[@]}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --address) ADDRESS="$2"; shift 2 ;;
    --venv) VENV_DIR="$2"; shift 2 ;;
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    --log-dir) LOG_DIR="$2"; shift 2 ;;
    --wait-seconds) WAIT_SECONDS="$2"; shift 2 ;;
    --allow-non-loopback) ALLOW_NON_LOOPBACK=1; shift ;;
    --refresh-dependencies) REFRESH_DEPENDENCIES=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

for command_name in curl grep mkdir nohup rm sleep ss; do need_command "${command_name}"; done
validate_port "${PORT}"
validate_address "${ADDRESS}"
if [[ ! "${WAIT_SECONDS}" =~ ^[0-9]+$ ]] || (( WAIT_SECONDS < 5 || WAIT_SECONDS > 600 )); then
  die "wait seconds must be between 5 and 600"
fi
if [[ "${ADDRESS}" != "127.0.0.1" && "${ADDRESS}" != "localhost" && "${ADDRESS}" != "::1" && ${ALLOW_NON_LOOPBACK} -ne 1 ]]; then
  die "non-loopback binding requires --allow-non-loopback and an authenticated TLS reverse proxy/firewall"
fi
if [[ ${REFRESH_DEPENDENCIES} -eq 1 ]]; then
  [[ -x "${VENV_DIR}/bin/python" ]] || die "virtual environment does not exist: ${VENV_DIR}"
  "${VENV_DIR}/bin/python" -m pip install -r "${PROJECT_ROOT}/requirements.txt"
fi
[[ -x "${VENV_DIR}/bin/streamlit" ]] || die "Streamlit is not installed; run scripts/install-manual.sh first"
mkdir -p -- "${RUN_DIR}" "${LOG_DIR}"
PID_FILE="${RUN_DIR}/streamlit-standalone-${PORT}.pid"
LOG_FILE="${LOG_DIR}/streamlit-standalone-${PORT}.log"
if [[ -r "${PID_FILE}" ]]; then
  read -r EXISTING_PID <"${PID_FILE}"
  if [[ "${EXISTING_PID}" =~ ^[0-9]+$ ]] && kill -0 "${EXISTING_PID}" 2>/dev/null; then
    die "standalone Streamlit already runs with PID ${EXISTING_PID}"
  fi
  rm -f -- "${PID_FILE}"
fi
if ss -H -ltn "sport = :${PORT}" | grep -q .; then
  die "port ${PORT} is already occupied"
fi

cd "${PROJECT_ROOT}"
nohup "${VENV_DIR}/bin/streamlit" run "${PROJECT_ROOT}/app.py" \
  --server.address="${ADDRESS}" \
  --server.port="${PORT}" \
  --server.headless=true \
  --browser.gatherUsageStats=false \
  >>"${LOG_FILE}" 2>&1 &
STREAMLIT_PID=$!
printf '%s\n' "${STREAMLIT_PID}" >"${PID_FILE}"
chmod 0600 "${PID_FILE}"

HEALTH_HOST="$(health_host "${ADDRESS}")"
for (( elapsed=0; elapsed<WAIT_SECONDS; elapsed+=2 )); do
  if ! kill -0 "${STREAMLIT_PID}" 2>/dev/null; then
    rm -f -- "${PID_FILE}"
    die "Streamlit exited during startup; inspect ${LOG_FILE}"
  fi
  if curl --fail --silent "http://${HEALTH_HOST}:${PORT}/_stcore/health" >/dev/null; then
    printf 'Standalone Streamlit started.\nPID: %s\nURL: http://%s:%s\nLog: %s\n' "${STREAMLIT_PID}" "${ADDRESS}" "${PORT}" "${LOG_FILE}"
    exit 0
  fi
  sleep 2
done
kill -TERM "${STREAMLIT_PID}" 2>/dev/null || true
rm -f -- "${PID_FILE}"
die "Streamlit did not become healthy within ${WAIT_SECONDS} seconds; inspect ${LOG_FILE}"
