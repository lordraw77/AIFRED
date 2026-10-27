# Shared helpers for the deployment scripts. Source it; do not run it.
# shellcheck shell=bash

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env}"
COMPOSE="${COMPOSE:-docker compose}"
export COMPOSE_PROGRESS="${COMPOSE_PROGRESS:-quiet}"   # errors still show; set to auto for full progress
UNSEAL_KEY_FILE="${OPENBAO_UNSEAL_KEY_FILE:-./openbao-unseal.key}"
ROOT_TOKEN_FILE="${OPENBAO_ROOT_TOKEN_FILE:-./openbao-root.token}"

# every script reports where it stopped instead of exiting silently
trap 'rc=$?; [ "$rc" -eq 0 ] || printf "ERROR: %s stopped (exit %s); it is safe to run it again\n" "$(basename "$0")" "$rc" >&2' EXIT

say()  { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_docker() {
  command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH"
  docker compose version >/dev/null 2>&1 || die "the Docker Compose plugin is missing (docker compose)"
}

# env_get KEY -> value of the last KEY=value line in the env file (empty if absent)
env_get() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n -E "s/^${1}=(.*)$/\1/p" "$ENV_FILE" | tail -n 1
}

# env_set KEY VALUE -> replace KEY=... in place, or append it; the file stays mode 0600
env_set() {
  local key=$1 value=$2 tmp
  tmp="$(umask 077; mktemp "${ENV_FILE}.XXXXXX")"
  if grep -q -E "^${key}=" "$ENV_FILE" 2>/dev/null; then
    KEY="$key" VALUE="$value" awk -F= 'BEGIN{k=ENVIRON["KEY"]; v=ENVIRON["VALUE"]} $1==k {print k "=" v; next} {print}' "$ENV_FILE" >"$tmp"
  else
    { cat "$ENV_FILE" 2>/dev/null; printf '%s=%s\n' "$key" "$value"; } >"$tmp"
  fi
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
}

random_token() { # LENGTH
  # `head` closes the pipe once it has enough, so `tr` gets SIGPIPE; that is expected, not an error
  LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-24}" || true
}

# wait_for "description" SECONDS command...  -> poll until the command succeeds
wait_for() {
  local what=$1 timeout=$2 i=0
  shift 2
  until "$@" >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -ge "$timeout" ] && die "timed out after ${timeout}s waiting for $what"
    sleep 1
  done
}

service_health() { # SERVICE -> healthy | unhealthy | starting | none
  local id
  id="$($COMPOSE ps -q "$1" 2>/dev/null | head -n 1)"
  [ -n "$id" ] || { echo none; return 0; }
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id"
}

is_healthy() { [ "$(service_health "$1")" = "healthy" ]; }

# bao ARGS... -> run the OpenBao CLI inside the openbao container (token from $BAO_TOKEN, passed by name only)
bao() { $COMPOSE exec -T -e BAO_TOKEN openbao bao "$@"; }

# bao_status_field FIELD -> true/false from `bao status -format=json` (exit code 2 = sealed is fine)
bao_status_field() {
  bao status -format=json 2>/dev/null | sed -n -E "s/^[[:space:]]*\"$1\": *(true|false).*/\1/p" | head -n 1
}

# unseal_with_file -> feed the keys in $UNSEAL_KEY_FILE to OpenBao until it is unsealed
unseal_with_file() {
  [ -s "$UNSEAL_KEY_FILE" ] || return 1
  local key
  while IFS= read -r key || [ -n "$key" ]; do
    [ -n "$key" ] || continue
    BAO_TOKEN='' bao operator unseal "$key" >/dev/null || return 1
    [ "$(bao_status_field sealed)" = "false" ] && return 0
  done <"$UNSEAL_KEY_FILE"
  [ "$(bao_status_field sealed)" = "false" ]
}

# compose_project -> the Compose project name (COMPOSE_PROJECT_NAME, else the `name:` of the compose file)
compose_project() {
  if [ -n "${COMPOSE_PROJECT_NAME:-}" ]; then
    printf '%s\n' "$COMPOSE_PROJECT_NAME"
    return 0
  fi
  $COMPOSE config --format json | sed -n -E 's/^[[:space:]]{2}"name": *"([^"]+)".*/\1/p' | head -n 1
}
