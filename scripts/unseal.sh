#!/usr/bin/env bash
# Unseal OpenBao after a restart. Uses ./openbao-unseal.key if it exists, otherwise asks for the key
# (input is not echoed). AIFRED notices on its own once OpenBao is unsealed.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

require_docker
$COMPOSE up -d openbao >/dev/null
wait_for "OpenBao to answer" 90 is_healthy openbao

if [ "$(bao_status_field sealed)" = "false" ]; then
  say "OpenBao is already unsealed."
  exit 0
fi

if unseal_with_file; then
  say "OpenBao unsealed with $UNSEAL_KEY_FILE."
  exit 0
fi

say "Enter the unseal key (several keys: run again for each; input is hidden)."
while [ "$(bao_status_field sealed)" = "true" ]; do
  read -r -s -p "Unseal key: " key; echo
  [ -n "$key" ] || die "no key given"
  BAO_TOKEN='' bao operator unseal "$key" >/dev/null || die "that key was rejected"
  unset key
done
say "OpenBao unsealed."
