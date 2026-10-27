#!/usr/bin/env bash
# BREAK-GLASS: print a new OpenBao root token on stdout (everything else goes to stderr).
#
# scripts/setup.sh revokes the root token after configuring OpenBao, and since OpenBao 2.5.3 the
# unauthenticated root-generation endpoints are off. To use this script:
#   1. in deploy/openbao/config.hcl, uncomment `disable_unauthed_generate_root_endpoints = false`
#   2. docker compose restart openbao && scripts/unseal.sh
#   3. ROOT=$(scripts/openbao-root-token.sh)       (needs the unseal key(s), like unsealing does)
#   4. when you are done:  BAO_TOKEN=$ROOT docker compose exec -T -e BAO_TOKEN openbao bao token revoke -self
#      and comment the setting out again, then restart and unseal.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

require_docker
[ "$(bao_status_field sealed)" = "false" ] || die "OpenBao is sealed; run scripts/unseal.sh first"

BAO_TOKEN='' bao delete sys/generate-root/attempt >/dev/null 2>&1 || true   # drop an unfinished attempt
attempt="$(BAO_TOKEN='' bao write -f -format=json sys/generate-root/attempt 2>&1)" || {
  echo "$attempt" | tail -n 4 >&2
  die "the unauthenticated endpoints are disabled: follow steps 1 and 2 in the header of this script"
}
nonce="$(printf '%s\n' "$attempt" | sed -n -E 's/.*"nonce": *"([^"]+)".*/\1/p')"
otp="$(printf '%s\n' "$attempt" | sed -n -E 's/.*"otp": *"([^"]+)".*/\1/p')"
[ -n "$nonce" ] && [ -n "$otp" ] || die "unexpected answer from OpenBao"

encoded=""
submit() { # KEY -> sets $encoded once the quorum is reached
  local out
  out="$(BAO_TOKEN='' bao write -format=json sys/generate-root/update key="$1" nonce="$nonce" 2>&1)" \
    || { BAO_TOKEN='' bao delete sys/generate-root/attempt >/dev/null 2>&1 || true; die "that key was rejected"; }
  printf '%s\n' "$out" | grep -q '"complete": *true' \
    && encoded="$(printf '%s\n' "$out" | sed -n -E 's/.*"encoded_token": *"([^"]+)".*/\1/p')"
  return 0
}
if [ -s "$UNSEAL_KEY_FILE" ]; then
  while IFS= read -r key || [ -n "$key" ]; do
    [ -n "$key" ] && [ -z "$encoded" ] && submit "$key"
  done <"$UNSEAL_KEY_FILE"
fi
while [ -z "$encoded" ]; do
  read -r -s -p "Unseal key: " key >&2; echo >&2
  [ -n "$key" ] || die "no key given"
  submit "$key"
done

# the token comes back XOR-ed with the one-time password, then base64-encoded; decode it inside the aifred container
ENCODED="$encoded" OTP="$otp" $COMPOSE exec -T -e ENCODED -e OTP aifred python -c "
import base64, os
enc, otp = os.environ['ENCODED'], os.environ['OTP'].encode()
raw = base64.b64decode(enc + '=' * (-len(enc) % 4))
print(bytes(a ^ b for a, b in zip(raw, otp)).decode())"
echo "Root token printed above. Revoke it when you are done (see the header of this script)." >&2
