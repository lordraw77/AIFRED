#!/usr/bin/env bash
# Back up the AIFRED data volume and the OpenBao volume into separate archives.
# They are kept apart on purpose: either one alone cannot be used to read the stored secrets.
#
#   scripts/backup.sh [aifred|openbao|all] [DIRECTORY]      (default: all, ./backups)
#
# The service being copied is stopped for a consistent copy and started again; OpenBao comes back
# sealed (it is unsealed again here when ./openbao-unseal.key exists).
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

what="${1:-all}"
dest="${2:-./backups}"
case "$what" in aifred | openbao | all) ;; *) die "usage: $0 [aifred|openbao|all] [directory]" ;; esac
require_docker
mkdir -p "$dest"
chmod 700 "$dest"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
project="$(compose_project)"
[ -n "$project" ] || die "cannot determine the compose project name"

# archive VOLUME_SUFFIX SERVICE FILE -> tar the volume through a throw-away container of the service's image
archive() {
  local volume="${project}_$1" service=$2 file=$3 image
  image="$($COMPOSE config --images "$service" | head -n 1)"
  docker volume inspect "$volume" >/dev/null 2>&1 || die "volume $volume does not exist yet"
  $COMPOSE stop "$service" >/dev/null
  if ! (umask 077; docker run --rm --entrypoint tar -v "$volume":/v:ro "$image" -C /v -czf - . >"$file"); then
    rm -f "$file"
    $COMPOSE start "$service" >/dev/null || true
    die "could not archive $volume"
  fi
  $COMPOSE start "$service" >/dev/null
  say "  wrote $file ($(du -h "$file" | cut -f1))"
}

if [ "$what" = aifred ] || [ "$what" = all ]; then
  say "Backing up the AIFRED data volume..."
  archive aifred-data aifred "$dest/aifred-data-$stamp.tar.gz"
fi
if [ "$what" = openbao ] || [ "$what" = all ]; then
  say "Backing up OpenBao (storage and audit log)..."
  archive openbao-data openbao "$dest/openbao-data-$stamp.tar.gz"
  wait_for "OpenBao to answer" 90 is_healthy openbao
  unseal_with_file && say "  OpenBao unsealed again" || warn "OpenBao is sealed: run scripts/unseal.sh"
fi
say "Done. Store the two archives in different places, and keep the OpenBao unseal key separate from both."
