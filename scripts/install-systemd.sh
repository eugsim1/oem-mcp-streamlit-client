#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

SERVICE_USER="oracle"
SERVICE_GROUP="oinstall"
INSTALL_DIR="${PROJECT_ROOT}"
VENV_DIR="/opt/oem-mcp-streamlit/venv"
ENV_FILE="/etc/oem-mcp-streamlit/oem-mcp-streamlit.env"
DATA_DIR="/var/lib/oem-mcp-streamlit"
LOG_DIR="/var/log/oem-mcp-streamlit"
ADDRESS="127.0.0.1"
PORT="8501"
PYTHON_BIN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service-user) SERVICE_USER="$2"; shift 2 ;;
    --service-group) SERVICE_GROUP="$2"; shift 2 ;;
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --venv) VENV_DIR="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --log-dir) LOG_DIR="$2"; shift 2 ;;
    --address) ADDRESS="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ "$(id -u)" -eq 0 ]] || die "run this installer with sudo"
for command_name in getent id install mktemp runuser sed systemctl; do need_command "${command_name}"; done
validate_port "${PORT}"
validate_address "${ADDRESS}"
[[ "${ADDRESS}" == "127.0.0.1" || "${ADDRESS}" == "localhost" || "${ADDRESS}" == "::1" ]] || die "systemd installation defaults to loopback; place an authenticated TLS reverse proxy in front before changing it"
getent group "${SERVICE_GROUP}" >/dev/null || die "service group does not exist: ${SERVICE_GROUP}"
id "${SERVICE_USER}" >/dev/null 2>&1 || die "service user does not exist: ${SERVICE_USER}"
SERVICE_USER_GROUPS="$(id -Gn "${SERVICE_USER}")"
[[ " ${SERVICE_USER_GROUPS} " == *" ${SERVICE_GROUP} "* ]] || die "${SERVICE_USER} is not a member of ${SERVICE_GROUP}"

INSTALL_DIR="$(cd -- "${INSTALL_DIR}" && pwd -P)"
[[ -r "${INSTALL_DIR}/requirements.txt" && -r "${INSTALL_DIR}/app.py" ]] || die "install directory is not an OEM MCP Streamlit checkout: ${INSTALL_DIR}"
if [[ -z "${PYTHON_BIN}" ]]; then PYTHON_BIN="$(resolve_python)"; fi
[[ -x "${PYTHON_BIN}" ]] || die "Python is not executable: ${PYTHON_BIN}"

install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "$(dirname -- "${VENV_DIR}")" "${DATA_DIR}" "${LOG_DIR}"
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  runuser -u "${SERVICE_USER}" -- "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi
runuser -u "${SERVICE_USER}" -- "${VENV_DIR}/bin/python" -m pip install --upgrade pip
runuser -u "${SERVICE_USER}" -- "${VENV_DIR}/bin/python" -m pip install -r "${INSTALL_DIR}/requirements.txt"

install -d -m 0750 -o root -g "${SERVICE_GROUP}" "$(dirname -- "${ENV_FILE}")"
if [[ ! -e "${ENV_FILE}" ]]; then
  install -m 0640 -o root -g "${SERVICE_GROUP}" "${INSTALL_DIR}/config/oem-mcp-streamlit.env.example" "${ENV_FILE}"
  printf 'Created service environment file: %s\n' "${ENV_FILE}"
  printf 'Edit OEM_MCP_ENDPOINT and optional non-secret defaults before starting. Leave OEM_MCP_PASSWORD empty to enter it in the GUI.\n'
else
  printf 'Preserved existing service environment file: %s\n' "${ENV_FILE}"
fi
sed -i \
  -e "s|^STREAMLIT_ADDRESS=.*|STREAMLIT_ADDRESS=${ADDRESS}|" \
  -e "s|^STREAMLIT_PORT=.*|STREAMLIT_PORT=${PORT}|" \
  -e "s|^OEM_MCP_DATA_DIR=.*|OEM_MCP_DATA_DIR=${DATA_DIR}|" \
  -e "s|^OEM_MCP_LOG_DIR=.*|OEM_MCP_LOG_DIR=${LOG_DIR}|" \
  -e "s|^OEM_MCP_PROFILE_FILE=.*|OEM_MCP_PROFILE_FILE=${DATA_DIR}/profiles.json|" \
  "${ENV_FILE}"

UNIT_SOURCE="${INSTALL_DIR}/systemd/oem-mcp-streamlit.service"
UNIT_TARGET="/etc/systemd/system/oem-mcp-streamlit.service"
TEMP_UNIT="$(mktemp)"
trap 'rm -f -- "${TEMP_UNIT}"' EXIT
sed \
  -e "s|@@SERVICE_USER@@|${SERVICE_USER}|g" \
  -e "s|@@SERVICE_GROUP@@|${SERVICE_GROUP}|g" \
  -e "s|@@INSTALL_DIR@@|${INSTALL_DIR}|g" \
  -e "s|@@VENV_DIR@@|${VENV_DIR}|g" \
  -e "s|@@ENV_FILE@@|${ENV_FILE}|g" \
  -e "s|@@DATA_DIR@@|${DATA_DIR}|g" \
  -e "s|@@LOG_DIR@@|${LOG_DIR}|g" \
  -e "s|@@ADDRESS@@|${ADDRESS}|g" \
  -e "s|@@PORT@@|${PORT}|g" \
  "${UNIT_SOURCE}" >"${TEMP_UNIT}"
install -m 0644 -o root -g root "${TEMP_UNIT}" "${UNIT_TARGET}"
systemctl daemon-reload
systemctl enable oem-mcp-streamlit.service
printf '\nInstallation complete; the service has not been started.\n'
printf '1. Review: sudo vi %s\n' "${ENV_FILE}"
printf '2. Start:  sudo %s/scripts/start-service.sh\n' "${INSTALL_DIR}"
printf '3. Test:   sudo %s/scripts/smoke-test.sh --port %s\n' "${INSTALL_DIR}" "${PORT}"
