#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC2034
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is missing: $1"
}

require_root() {
  [[ "$(id -u)" -eq 0 ]] || die "run this script as root (sudo)"
}

validate_port() {
  local port="$1"
  [[ "${port}" =~ ^[0-9]+$ ]] || die "port must be numeric"
  (( port >= 1024 && port <= 65535 )) || die "port must be between 1024 and 65535"
}

validate_address() {
  local address="$1"
  [[ "${address}" =~ ^[A-Za-z0-9.:-]+$ ]] || die "address contains invalid characters"
}

resolve_python() {
  local requested="${1:-}"
  local candidate
  if [[ -n "${requested}" ]]; then
    command -v "${requested}" >/dev/null 2>&1 || die "Python executable was not found: ${requested}"
    candidate="$(command -v "${requested}")"
  else
    for candidate in python3.11 python3.10 python3.9; do
      if command -v "${candidate}" >/dev/null 2>&1; then
        candidate="$(command -v "${candidate}")"
        break
      fi
    done
  fi
  [[ -n "${candidate:-}" && -x "${candidate}" ]] || die "Python 3.9 or newer is required"
  "${candidate}" -c 'import sys; raise SystemExit(0 if (3, 9) <= sys.version_info[:2] < (3, 14) else 1)' || \
    die "Python must be version 3.9 through 3.13"
  printf '%s\n' "${candidate}"
}

load_env_file() {
  local env_file="$1"
  [[ -r "${env_file}" ]] || die "environment file is not readable: ${env_file}"
  set -a
  # shellcheck disable=SC1090
  source "${env_file}"
  set +a
}

health_host() {
  local address="$1"
  if [[ "${address}" == *:* ]]; then
    printf '[%s]' "${address}"
  else
    printf '%s' "${address}"
  fi
}
