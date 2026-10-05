#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# This project is unique to this run. Cleanup cannot remove the normal app volume.
project="zenimport-check-$$"
dc() { docker compose -p "$project" -f "$root/compose.yaml" -f "$root/compose.test.yaml" "$@"; }
cleanup() {
  status=$?
  trap - EXIT
  dc down --volumes --rmi local --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
dc --profile test build backend backend-tests parser-tests smoke browser-backend browser-tests
dc up -d --wait --wait-timeout 90 backend
dc run --rm backend-tests
dc run --rm parser-tests
dc run --rm smoke python tests/container_smoke.py seed
dc up -d --force-recreate --wait --wait-timeout 90 backend
dc run --rm smoke python tests/container_smoke.py verify
dc up -d --wait --wait-timeout 90 browser-backend
dc run --rm browser-tests
printf '%s\n' 'PASS: all Docker checks; only this run’s disposable containers and volume will be removed.'
