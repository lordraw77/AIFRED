#!/usr/bin/env bash
# Restore one volume from an archive made by scripts/backup.sh. DESTRUCTIVE: the volume's current
# content is replaced.
#
#   scripts/restore.sh aifred|openbao ARCHIVE [--yes]
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

what="${1:-}"; archive_file="${2:-}"; confirm="${3:-}"
case "$what" in aifred | openbao) ;; *) die "usage: $0 aifred|openbao ARCHIVE [--yes]" ;; esac
[ -f "$archive_file" ] || die "archive not found: $archive_file"
require_docker
if [ "$confirm" != "--yes" ]; then
  read -r -p "This replaces the current $what volume with $archive_file. Type 'yes' to continue: " answer
  [ "$answer" = "yes" ] || die "cancelled"
fi

project="$(compose_project)"
case "$what" in
  aifred)  volume="${project}_aifred-data"  service=aifred ;;
  openbao) volume="${project}_openbao-data" service=openbao ;;
esac
image="$($COMPOSE config --images "$service" | head -n 1)"
$COMPOSE stop "$service" >/dev/null 2>&1 || true
docker volume create "$volume" >/dev/null
# clear the volume, then unpack; ownership and modes come from the archive
docker run --rm --user 0 --entrypoint sh -v "$volume":/v -v "$(cd "$(dirname "$archive_file")" && pwd)/$(basename "$archive_file")":/backup.tgz:ro "$image" \
  -c 'find /v -mindepth 1 -delete && tar -C /v -xzf /backup.tgz'
$COMPOSE up -d "$service" >/dev/null
if [ "$what" = openbao ]; then
  wait_for "OpenBao to answer" 90 is_healthy openbao
  unseal_with_file && say "OpenBao unsealed" || warn "OpenBao is sealed: run scripts/unseal.sh"
fi
say "Restored $what from $archive_file."
