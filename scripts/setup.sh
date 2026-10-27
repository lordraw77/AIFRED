#!/usr/bin/env bash
# First-run setup for the AIFRED stack. Safe to run again: every step checks what is already done.
#
#   1. creates .env from .env.example (mode 0600) and fills in what is missing
#   2. starts OpenBao, initialises it (keeps the unseal key in ./openbao-unseal.key) and unseals it
#   3. creates the Transit key, a least-privilege policy and an AppRole for AIFRED, then revokes the root token
#   4. builds and starts AIFRED, then removes the bootstrap admin password from .env
#
# Variables: OPENBAO_KEY_SHARES (default 1), OPENBAO_KEY_THRESHOLD (default 1), KEEP_ADMIN_PASSWORD=1
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

SHARES="${OPENBAO_KEY_SHARES:-1}"
THRESHOLD="${OPENBAO_KEY_THRESHOLD:-1}"
PENDING_PASSWORD=""   # the bootstrap password, shown once at the end

require_docker

# 1. environment file ---------------------------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
  (umask 077; cp .env.example "$ENV_FILE")
  say "Created $ENV_FILE from .env.example"
fi
chmod 600 "$ENV_FILE"
[ -n "$(env_get AIFRED_ADMIN_USER)" ] || env_set AIFRED_ADMIN_USER admin
[ -n "$(env_get SEARXNG_SECRET)" ] || env_set SEARXNG_SECRET "$(random_token 48)"
if [ "$(env_get AIFRED_SETUP_ADMIN_CREATED)" != "yes" ]; then
  [ -n "$(env_get AIFRED_ADMIN_PASSWORD)" ] || env_set AIFRED_ADMIN_PASSWORD "$(random_token 20)"
  # also picks up a password left by an earlier run that stopped before it could be shown
  PENDING_PASSWORD="$(env_get AIFRED_ADMIN_PASSWORD)"
fi

# 2. OpenBao: start, initialise, unseal ---------------------------------------------------------
say "Starting OpenBao..."
$COMPOSE up -d openbao
wait_for "OpenBao to answer" 90 is_healthy openbao

if [ "$(bao_status_field initialized)" != "true" ]; then
  say "Initialising OpenBao ($SHARES key share(s), $THRESHOLD needed to unseal)..."
  init_json="$(BAO_TOKEN='' bao operator init -key-shares="$SHARES" -key-threshold="$THRESHOLD" -format=json)"
  # keep the unseal keys and the root token on disk at once, so a failure further down loses nothing
  (umask 077
   printf '%s\n' "$init_json" | awk -F'"' '/"unseal_keys_b64"/{f=1;next} f&&/\]/{f=0} f&&NF>=2{print $2}' >"$UNSEAL_KEY_FILE"
   printf '%s\n' "$init_json" | sed -n -E 's/^[[:space:]]*"root_token": *"([^"]+)".*/\1/p' >"$ROOT_TOKEN_FILE")
  unset init_json
  [ -s "$UNSEAL_KEY_FILE" ] && [ -s "$ROOT_TOKEN_FILE" ] || die "could not read the keys from the OpenBao init output"
  say "  unseal key(s) saved in $UNSEAL_KEY_FILE"
fi

if [ "$(bao_status_field sealed)" = "true" ]; then
  say "Unsealing OpenBao..."
  unseal_with_file || die "OpenBao is sealed and $UNSEAL_KEY_FILE is missing or wrong; run scripts/unseal.sh"
fi

# 3. Transit key, policy, AppRole ---------------------------------------------------------------
if [ -s "$ROOT_TOKEN_FILE" ]; then
  say "Configuring OpenBao for AIFRED..."
  BAO_TOKEN="$(cat "$ROOT_TOKEN_FILE")"
  export BAO_TOKEN
  bao secrets list | grep -q '^transit/' || bao secrets enable transit >/dev/null
  bao read transit/keys/aifred >/dev/null 2>&1 || bao write -f transit/keys/aifred type=aes256-gcm96 >/dev/null
  printf '%s\n' \
    'path "transit/encrypt/aifred" { capabilities = ["update"] }' \
    'path "transit/decrypt/aifred" { capabilities = ["update"] }' | bao policy write aifred - >/dev/null
  bao auth list | grep -q '^approle/' || bao auth enable approle >/dev/null
  bao write auth/approle/role/aifred token_policies=aifred token_ttl=1h token_max_ttl=4h \
      secret_id_ttl=0 secret_id_num_uses=0 >/dev/null
  env_set AIFRED_OPENBAO_ROLE_ID "$(bao read -field=role_id auth/approle/role/aifred/role-id)"
  [ -n "$(env_get AIFRED_OPENBAO_SECRET_ID)" ] \
    || env_set AIFRED_OPENBAO_SECRET_ID "$(bao write -f -field=secret_id auth/approle/role/aifred/secret-id)"
  bao token revoke -self >/dev/null
  unset BAO_TOKEN
  rm -f "$ROOT_TOKEN_FILE"
  say "  Transit key, policy and AppRole ready; the root token was revoked"
fi
[ -n "$(env_get AIFRED_OPENBAO_ROLE_ID)" ] && [ -n "$(env_get AIFRED_OPENBAO_SECRET_ID)" ] \
  || die "OpenBao is initialised but AIFRED has no AppRole credentials in $ENV_FILE. Generate a root token with 'bao operator generate-root' and see docs/deploy.md (Recovery)."

# 4. AIFRED --------------------------------------------------------------------------------------
say "Building the AIFRED image..."
build_log="$(mktemp)"
if [ "${AIFRED_PULL:-}" = "1" ]; then
  $COMPOSE pull aifred >"$build_log" 2>&1 || { tail -n 40 "$build_log" >&2; rm -f "$build_log"; die "could not pull the image (is AIFRED_IMAGE set, for example to lordraw/aifred?)"; }
else
  $COMPOSE build aifred >"$build_log" 2>&1 || { tail -n 40 "$build_log" >&2; rm -f "$build_log"; die "the image build failed"; }
fi
rm -f "$build_log"
say "Starting AIFRED..."
$COMPOSE up -d aifred
wait_for "AIFRED to become healthy" 120 is_healthy aifred \
  || { $COMPOSE logs --tail 30 aifred >&2; die "AIFRED did not become healthy"; }

if [ "$(env_get AIFRED_SETUP_ADMIN_CREATED)" != "yes" ]; then
  env_set AIFRED_SETUP_ADMIN_CREATED yes
  if [ "${KEEP_ADMIN_PASSWORD:-0}" != "1" ]; then
    env_set AIFRED_ADMIN_PASSWORD ""
    $COMPOSE up -d aifred >/dev/null   # recreates the container without the password in its environment
    wait_for "AIFRED to restart" 120 is_healthy aifred
  fi
fi

port="${AIFRED_PORT:-$(env_get AIFRED_PORT)}"; bind="${AIFRED_BIND:-$(env_get AIFRED_BIND)}"
say ""
say "AIFRED is running: http://${bind:-127.0.0.1}:${port:-8700}"
say "  user:     $(env_get AIFRED_ADMIN_USER)"
if [ -n "$PENDING_PASSWORD" ]; then
  if [ "${KEEP_ADMIN_PASSWORD:-0}" = "1" ]; then
    say "  password: $PENDING_PASSWORD     <- kept in $ENV_FILE (KEEP_ADMIN_PASSWORD=1); remove it once you have changed it"
  else
    say "  password: $PENDING_PASSWORD     <- shown once; it is no longer stored in $ENV_FILE"
  fi
fi
say ""
say "Keep safe (they are not in the repository):"
say "  $UNSEAL_KEY_FILE   unseals OpenBao after every restart. Copy it to your password manager."
say "                      Deleting it from this machine is safer; then run scripts/unseal.sh and paste the key."
say "  $ENV_FILE           holds the AppRole credentials of AIFRED (mode 0600)."
say "Back up the AIFRED data and OpenBao separately: scripts/backup.sh"
