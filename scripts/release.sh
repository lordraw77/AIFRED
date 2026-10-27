#!/usr/bin/env bash
# Prints the commands for a release; it runs none of them.
#
#   scripts/release.sh [REGISTRY/IMAGE]      for example: scripts/release.sh registry.example.com/aifred
set -euo pipefail
cd "$(dirname "$0")/.."

version="$(sed -n -E 's/^version = "([^"]+)".*/\1/p' pyproject.toml | head -n 1)"
image="${1:-lordraw/aifred}"
[ -n "$version" ] || { echo "cannot read the version from pyproject.toml" >&2; exit 1; }

cat <<MSG
Release ${version}

1. Checks (run these first):
   make lint test docker/build

2. Git (run them yourself):
   git tag -a v${version} -m "AIFRED ${version}"
   git push origin v${version}

3. Image:
   docker build --build-arg VERSION=${version} -t ${image}:${version} -t ${image}:latest .
   docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy image --severity HIGH,CRITICAL --ignore-unfixed ${image}:${version}
   docker push ${image}:${version} && docker push ${image}:latest

4. Deployment: set AIFRED_VERSION=${version} (and AIFRED_IMAGE=${image} to use the published image) in .env and run 'AIFRED_PULL=1 make docker/run'.
MSG
