#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
for platform in linux/arm64 linux/amd64; do
  "$root/scripts/test-docker.sh" "$platform"
done
