#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
platform=${1:-$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}')}
case "$platform" in linux/amd64|linux/arm64) ;; *) echo 'Expected linux/amd64 or linux/arm64' >&2; exit 2;; esac
revision=$(git -C "$root" rev-parse HEAD)
short=$(git -C "$root" rev-parse --short=12 HEAD)
image=${ZENIMPORT_IMAGE:-zenimport-backend:local-$short}
docker build --platform "$platform" --target final --build-arg "VCS_REF=$revision" \
 --build-arg "SOURCE_URL=${ZENIMPORT_SOURCE_URL:-https://github.com/vaslv/zenimport-backend}" --tag "$image" "$root"
echo "Built $image for $platform locally; nothing pushed."
