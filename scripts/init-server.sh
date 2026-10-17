#!/bin/sh
set -eu
: "${ZENIMPORT_IMAGE:?Set the verified local or published image reference}"
test ! -e .env || { echo '.env already exists; left unchanged' >&2; exit 1; }
docker run --rm --network none --read-only --user "$(id -u):$(id -g)" \
 --env "ZENIMPORT_IMAGE=$ZENIMPORT_IMAGE" \
 --mount "type=bind,source=$PWD,target=/workspace" "$ZENIMPORT_IMAGE" \
 python /app/docker/init_server_env.py
