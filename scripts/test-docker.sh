#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project="zenimport-backend-check-$$"
config_image="$project-config"
API_TOKEN=fixture-backend-token-for-isolated-tests
ZENMONEY_TOKEN=
DATABASE_PATH=/data/zenimport.sqlite3
DATA_VOLUME="$project-data"
export API_TOKEN ZENMONEY_TOKEN DATABASE_PATH DATA_VOLUME
dc() { docker compose --env-file /dev/null -p "$project" -f "$root/compose.yaml" -f "$root/compose.test.yaml" "$@"; }
cleanup() {
  status=$?
  trap - EXIT
  if ! dc --profile test down --volumes --rmi local --remove-orphans; then
    printf '%s\n' "WARNING: cleanup failed for disposable project $project" >&2
    [ "$status" -ne 0 ] || status=1
  fi
  docker image rm "$config_image" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker build --target config -t "$config_image" "$root"
docker run --rm --network none --read-only --user 10001:10001 --tmpfs /workspace:mode=1777 "$config_image"
dc --profile test build backend tests smoke
dc run --rm --no-deps tests
dc up -d --wait --wait-timeout 60 backend
dc run --rm --no-deps smoke python tests/container_smoke.py seed
dc up -d --force-recreate --wait --wait-timeout 60 backend
dc run --rm --no-deps smoke python tests/container_smoke.py verify
printf '%s\n' 'PASS: standalone build, HTTP/auth, SDK and persistence; test data only.'
