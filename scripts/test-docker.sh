#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TEST_PLATFORM="${1:-$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}')}"
case "$TEST_PLATFORM" in linux/arm64|linux/amd64) ;; *) printf '%s\n' 'Expected linux/arm64 or linux/amd64' >&2; exit 2 ;; esac
[ "$#" -le 1 ] || { printf '%s\n' 'Usage: scripts/test-docker.sh [linux/arm64|linux/amd64]' >&2; exit 2; }
export TEST_PLATFORM
project="zenimport-backend-check-${TEST_PLATFORM#linux/}-$$"
config_image="$project-config"
API_TOKEN=fixture-backend-token-for-isolated-tests
ZENMONEY_TOKEN=
DATABASE_PATH=/data/zenimport.sqlite3
DATA_VOLUME="$project-data"
LIFECYCLE_DATABASE_PATH=/data/lifecycle.sqlite3
export API_TOKEN ZENMONEY_TOKEN DATABASE_PATH DATA_VOLUME LIFECYCLE_DATABASE_PATH
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
docker build --platform "$TEST_PLATFORM" --target config -t "$config_image" "$root"
docker run --platform "$TEST_PLATFORM" --rm --network none --read-only --user 10001:10001 --tmpfs /workspace:mode=1777 "$config_image"
dc --profile test build backend tests smoke lifecycle
dc run --rm --no-deps tests python tests/platform_check.py
dc run --rm --no-deps tests
dc up -d --wait --wait-timeout 60 backend
dc run --rm --no-deps smoke python tests/container_smoke.py seed
dc up -d --force-recreate --wait --wait-timeout 60 backend
dc run --rm --no-deps smoke python tests/container_smoke.py verify
dc up -d --wait --wait-timeout 90 lifecycle
dc run --rm --no-deps smoke python tests/lifecycle_smoke.py seed
dc kill -s SIGKILL lifecycle
dc up -d --wait --wait-timeout 90 lifecycle
dc run --rm --no-deps smoke python tests/lifecycle_smoke.py recovered
dc run --rm --no-deps smoke python tests/lifecycle_smoke.py graceful-seed
dc stop lifecycle &
stop_pid=$!
dc run --rm --no-deps smoke python tests/lifecycle_smoke.py release
wait "$stop_pid"
container=$(dc ps -aq lifecycle)
exit_code=$(docker inspect --format '{{.State.ExitCode}}' "$container")
case "$exit_code" in 0|143) ;; *) printf '%s\n' "Unexpected graceful-stop exit code: $exit_code" >&2; exit 1 ;; esac
docker logs "$container" 2>&1 | grep -q 'Application shutdown complete.'
printf '%s\n' "PASS: graceful application shutdown, exit code $exit_code"
dc up -d --wait --wait-timeout 90 lifecycle
dc run --rm --no-deps smoke python tests/lifecycle_smoke.py graceful
dc stop lifecycle
dc run --rm --no-deps smoke python tests/lifecycle_smoke.py snapshot
LIFECYCLE_DATABASE_PATH=/data/lifecycle-restored.sqlite3
export LIFECYCLE_DATABASE_PATH
dc up -d --force-recreate --wait --wait-timeout 90 lifecycle
dc run --rm --no-deps smoke python tests/lifecycle_smoke.py restored
printf '%s\n' "PASS: $TEST_PLATFORM build, tests, HTTP/auth, SIGKILL, graceful stop and restored delivery; test data only."
