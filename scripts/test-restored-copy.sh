#!/bin/sh
# Explicit offline rehearsal output only; never mounts the original volume.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
out=$(CDPATH= cd -- "${1:?Pass the private rehearsal directory}" && pwd)
image=${2:?Pass the locally built runtime image}
case "$out" in "$root"/data/rehearsal-*) ;; *) exit 1 ;; esac
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["restored_identical"] and r["source_copy_unchanged"] and not r["network_used"]' "$out/report.json"
test -f "$out/restored.sqlite3"
mkdir -p "$out/tmp"
fingerprint() {
 docker run --rm --network none --read-only --user 0:0 \
  --env SQLITE_TMPDIR=/scratch \
  --mount "type=bind,source=$out,target=/data,readonly" \
  --mount "type=bind,source=$out/tmp,target=/scratch" \
  --mount "type=bind,source=$root/scripts/check-financial-copy.py,target=/check.py,readonly" \
  "$image" python /check.py /data/restored.sqlite3
}
fingerprint > "$out/runtime-before.json"
task_name="zenimport-m2-restored-$$"
cleanup() { docker rm -f "$task_name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$task_name" --network none --read-only --user 0:0 \
 --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:size=32m \
 --mount "type=bind,source=$out,target=/data" \
 --env DATABASE_PATH=/data/restored.sqlite3 \
 --env API_TOKEN=fixture-backend-token-offline-copy-only --env ZENMONEY_TOKEN= \
 "$image" >/dev/null
docker exec "$task_name" python -c 'import json,time,urllib.request
for attempt in range(90):
 try:
  request=urllib.request.Request("http://127.0.0.1:8000/api/v1/info",headers={"Authorization":"Bearer fixture-backend-token-offline-copy-only"})
  info=json.load(urllib.request.urlopen(request,timeout=2))
  assert info["api_version"]==1 and info["zenmoney_state"]=="not_configured"
  assert urllib.request.urlopen("http://127.0.0.1:8000/health",timeout=2).status==200
  print("PASS: restored copy starts; HTTP health/API v1; SDK disabled; no external network")
  break
 except (OSError,AssertionError):time.sleep(1)
else:raise SystemExit("Runtime validation failed")'
docker stop --time 30 "$task_name" >/dev/null
fingerprint > "$out/runtime-after.json"
cmp "$out/runtime-before.json" "$out/runtime-after.json"
printf '%s\n' 'PASS: financial contents unchanged after application startup/shutdown'
