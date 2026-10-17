# ZenImport Backend on a remote server

The service runs the same way on Linux AMD64 and ARM64. The server needs Docker
Engine and Compose, not Python/Node or neighboring projects. One backend instance,
one local Docker volume containing SQLite. Do not expose public HTTP:
terminate HTTPS at your reverse proxy.

A clean database is acceptable for a new connection. **When moving an existing
server, transfer its database and delivery journal**, not only the token/ZenMoney
catalog: an empty database knows nothing about prior deletions or arbitrary
date/amount edits. Do not run old and new workers against the same ZenMoney account
simultaneously.

## Two installation methods

After source publication, build the image directly on the server:

```sh
git clone https://github.com/vaslv/zenimport-backend.git
cd zenimport-backend
# Select a verified release/commit, not a changing working branch.
./scripts/build-image.sh
```

This command builds only the native architecture, never pushes, and prints the local
image name. The image includes installed zenmoney-python 0.7.0 and dependencies;
an SDK checkout is unnecessary. Version/revision/source are recorded in OCI labels.

The second method uses a prebuilt image from a future GHCR release. Obtain the exact
published `ghcr.io/vaslv/zenimport-backend@sha256:...` from the release report. The image
is not yet published. The server needs only compose.server.yaml and init-server.sh
from the same verified release (or the entire checkout); no local build:

```sh
export ZENIMPORT_IMAGE=ghcr.io/vaslv/zenimport-backend@sha256:VERIFIED_DIGEST
docker pull "$ZENIMPORT_IMAGE"
```

A locally verified image can be transferred without a registry: `docker save` on
the build machine, private archive transfer, and `docker load` on the server. Its
architecture must match the server. Building linux/amd64 on ARM uses emulation;
this checks portability, not physical AMD64 server performance.

## Configuring a prebuilt image

Use a separate server directory. For a local build, set ZENIMPORT_IMAGE to the name
printed by build-image.sh. For a published image, use the verified digest above.
init-server.sh uses the prebuilt image itself and needs no host Python:

```sh
./scripts/init-server.sh
```

Creates .env (600) with a new random API_TOKEN, chosen image, and persistent
DATA_VOLUME=zenimport-server-data. Existing files are unchanged. Add ZENMONEY_TOKEN
in an editor; the extension needs API_TOKEN. Change DATA_VOLUME only when deliberately
selecting a separate new/restored volume. Never pass secrets as command arguments
or commit .env. `docker compose config` exposes interpolated secrets; do not publish
its output.

```sh
docker compose -f compose.server.yaml up -d --wait backend
docker compose -f compose.server.yaml ps
docker compose -f compose.server.yaml logs --tail 50 backend
```

`compose.server.yaml` uses image only, with no build. HTTP listens on 127.0.0.1:18792.
UID/GID 10001, read-only root, no capabilities, no-new-privileges, writable /data, and
temporary /tmp. Graceful stop allows 150 seconds; an SDK read can take 60 seconds
and retry once. Health checks storage, not completion of financial synchronization.

## HTTPS

Caddy example for **host installation**: deploy/Caddyfile.example. Replace the domain,
point DNS at the server, and allow inbound TCP 80/443. Caddy automatically issues/
renews certificates; its certificate/state storage must also persist. Existing
Nginx/Traefik can be used instead: pass Authorization and request bodies unchanged,
accept up to 2 MB, allow long responses, and never log tokens/bodies.
Do not expose internal port 18792 publicly.

After starting the proxy, check from the user's computer:

```sh
curl --fail https://YOUR_DOMAIN/health
```

Do not use -k or disable TLS validation. In the extension, enter root URL
`https://YOUR_DOMAIN` without a subpath and the new backend API_TOKEN, then approve
Chrome access to that host. ZENMONEY_TOKEN stays on the server. The actual hostname,
DNS, public certificate/proxy must be validated on your server; local acceptance
makes no claim that these are configured already.

## Moving an existing installation

1. Finish synchronization, stop the old backend, and never run it in parallel.
2. After stopping, export a consistent snapshot and keep a private copy. Do not copy only the main file of a running SQLite database without WAL.
3. Restore the snapshot into a new empty volume using [operations.md](operations.md), preserve the old volume, and select the restored volume in the server .env.
4. Start the new image, check health/version/catalog, and connect the extension to the new HTTPS address/token. First check an ordinary repeat synchronization.
5. Do not roll the journal back to an older copy after the new worker has sent records. Keep the same volume name during image updates. `down` preserves it; never use `down --volumes` in production.

Detailed snapshots/restoration/updates: [operations.md](operations.md).
Validated A/B and empty-database boundaries: [migration-lab.md](migration-lab.md).

## Prerelease image validation

```sh
./scripts/test-platforms.sh
./scripts/test-server-image.sh linux/arm64
./scripts/test-server-image.sh linux/amd64
```

These commands create their own projects/volumes with synthetic data and never
read production .env. Image-only checks have no ZenMoney token; they verify mode
600/no-overwrite bootstrap, SDK, HTTP/auth, UID/read-only root, loopback binding,
OCI revision, and database preservation on recreation. They remove only their own
test environment. Build the actual production image separately.
