#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ADDRESS=127.0.0.1
PORT=8501
while [[ $# -gt 0 ]]; do
  case "$1" in
    --address) ADDRESS="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done
need_command curl
validate_port "${PORT}"
validate_address "${ADDRESS}"
HOST="$(health_host "${ADDRESS}")"
curl --fail --silent --show-error "http://${HOST}:${PORT}/_stcore/health"
printf '\nStreamlit health check passed: http://%s:%s/_stcore/health\n' "${HOST}" "${PORT}"
