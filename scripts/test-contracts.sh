#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image="zenimport-contracts-check-$$"
trap 'docker image rm "$image" >/dev/null 2>&1 || true' EXIT
docker build --target backend-tests -t "$image" "$root"
docker run --rm --network none --read-only --tmpfs /tmp "$image" \
  python -m pytest -q -p no:cacheprovider tests/test_sync_contracts.py
