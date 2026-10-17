#!/bin/sh
# Exercise image-only deployment with a disposable database and no ZenMoney token.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
platform=${1:-$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}')}
case "$platform" in linux/amd64|linux/arm64) ;; *) exit 2;; esac
project="zenimport-server-check-${platform#linux/}-$$"
ZENIMPORT_IMAGE="$project:local"
API_TOKEN=fixture-server-image-token-never-real
ZENMONEY_TOKEN=
DATABASE_PATH=/data/zenimport.sqlite3
DATA_VOLUME="$project-data"
HTTP_PORT=0
DOCKER_DEFAULT_PLATFORM="$platform"
export ZENIMPORT_IMAGE API_TOKEN ZENMONEY_TOKEN DATABASE_PATH DATA_VOLUME HTTP_PORT DOCKER_DEFAULT_PLATFORM
workspace=$(mktemp -d)
dc() { docker compose --env-file /dev/null -p "$project" -f "$root/compose.server.yaml" "$@"; }
cleanup() {
 status=$?; trap - EXIT
 dc down --volumes --remove-orphans >/dev/null 2>&1 || status=1
 docker image rm "$ZENIMPORT_IMAGE" >/dev/null 2>&1 || true
 rm -rf "$workspace"
 exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$root/scripts/build-image.sh" "$platform"
(cd "$workspace" && "$root/scripts/init-server.sh")
docker run --rm --network none --read-only --mount "type=bind,source=$workspace,target=/input,readonly" \
 "$ZENIMPORT_IMAGE" python -c 'from pathlib import Path; import os; p=Path("/input/.env"); s=p.read_text(); assert p.stat().st_mode & 0o777 == 0o600; assert "ZENMONEY_TOKEN=\n" in s; assert "ZENIMPORT_IMAGE=" in s; assert len(next(x for x in s.splitlines() if x.startswith("API_TOKEN=")).split("=",1)[1])>=24'
before=$(docker run --rm --network none --read-only --mount "type=bind,source=$workspace,target=/input,readonly" "$ZENIMPORT_IMAGE" python -c 'from pathlib import Path; from hashlib import sha256; print(sha256(Path("/input/.env").read_bytes()).hexdigest())')
if (cd "$workspace" && "$root/scripts/init-server.sh"); then echo 'Unexpected .env overwrite' >&2; exit 1; fi
after=$(docker run --rm --network none --read-only --mount "type=bind,source=$workspace,target=/input,readonly" "$ZENIMPORT_IMAGE" python -c 'from pathlib import Path; from hashlib import sha256; print(sha256(Path("/input/.env").read_bytes()).hexdigest())')
[ "$before" = "$after" ]
dc up -d --pull never --wait --wait-timeout 60 backend
container=$(dc ps -q backend)
[ "$(docker inspect "$container" --format '{{.HostConfig.ReadonlyRootfs}}')" = true ]
[ "$(docker inspect "$container" --format '{{(index (index .HostConfig.PortBindings "8000/tcp") 0).HostIp}}')" = 127.0.0.1 ]
dc exec -T backend python -c 'import os,platform; from importlib.metadata import version; from pathlib import Path; import urllib.request,urllib.error; assert os.getuid()==10001; assert version("zenmoney-python")=="0.7.0"; assert Path("/app/LICENSE").is_file(); assert not Path("/app/.env").exists(); assert not Path("/app/tests").exists(); assert not Path("/app/scripts").exists(); assert urllib.request.urlopen("http://127.0.0.1:8000/health").status==200; r=urllib.request.Request("http://127.0.0.1:8000/api/v1/info",headers={"Authorization":"Bearer "+os.environ["API_TOKEN"]}); assert urllib.request.urlopen(r).status==200; print("PASS: image-only HTTP/auth, SDK, UID and runtime layout on "+platform.machine())'
first=$(dc exec -T backend python -c 'import sqlite3; c=sqlite3.connect("/data/zenimport.sqlite3"); print(c.execute("select value from service_meta where key=?",("instance_id",)).fetchone()[0])')
dc up -d --pull never --force-recreate --wait --wait-timeout 60 backend
second=$(dc exec -T backend python -c 'import sqlite3; c=sqlite3.connect("/data/zenimport.sqlite3"); print(c.execute("select value from service_meta where key=?",("instance_id",)).fetchone()[0])')
[ "$first" = "$second" ]
[ "$(docker image inspect "$ZENIMPORT_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$(git -C "$root" rev-parse HEAD)" ]
[ "$(docker image inspect "$ZENIMPORT_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.licenses"}}')" = MIT ]
echo "PASS: $platform image-only deployment, private bootstrap, no overwrite, persistent volume and OCI revision; no real data"
