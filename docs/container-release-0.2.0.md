# Backend 0.2.0 — container release and deployment

Version 0.2.0 adds [popup ZIP export/import](popup-migration.md), persistent source
pause, verified same-account restoration before the first sync, and a destination
delivery hold until explicit synchronization. Storage remains schema 6; the SDK
remains public PyPI `zenmoney-python==0.7.0`, pinned by version and SHA256.

Published image: `ghcr.io/vaslv/zenimport-backend:0.2.0` for Linux AMD64 and ARM64.
Immutable reference:
`ghcr.io/vaslv/zenimport-backend@sha256:4887d92e74ecee5454a5c84bc15ff586b8879849ce53006f05b56e63eff48d9f`.
Runtime source and Git tag `v0.2.0`:
`36694047a926cc231ca9e7731458df7f88b7c8ef`.

## Executed verification — 2026-10-17

[Native source CI](https://github.com/vaslv/zenimport-backend/actions/runs/39040271480)
passed on AMD64 and ARM64. [Publication and immutable-image acceptance](https://github.com/vaslv/zenimport-backend/actions/runs/39040282443)
also passed: each architecture ran 508 tests, HTTP/auth/persistence, SIGKILL,
graceful stop and snapshot restoration before publishing. After publication, both
native runners pulled the public digest anonymously and checked image-only setup,
private configuration/no overwrite, SDK 0.7.0, UID/read-only root, HTTP, persistent
volume and OCI source revision.

The public registry manifest was independently fetched without account credentials;
its SHA256 matches the reference above and includes both runtime architectures.
An independent anonymous pull on the development Mac also verified native ARM64,
UID 10001, installed SDK 0.7.0, and exact SHA256 equality of all 29 shipped backend
Python/SQL files against the tested source. This check used no data mounts/network
inside the container and did not start a production service.
The extension's [0.2.0 CI](https://github.com/vaslv/zenimport-chrome/actions/runs/39040271339)
passed; its reproducible ZIP is byte-identical to the local artifact. Separate
local integration: 18 browser/actual-backend tests passed, including exported ZIP,
server switch/import, invalid archive rejection, no duplicates, and preserved
remote deletion. All financial data/services were synthetic. No production
container/database or actual remote DNS/TLS/cutover was changed by publication.
Non-fatal upstream Starlette/httpx and Actions runtime deprecations remain visible
in the original logs; no assertions were disabled.

## Install on a new server

Docker Engine/Compose are required; no host Python, PyPI credentials, or GHCR
login is needed for the public image. From a new deployment directory:

```sh
git clone https://github.com/vaslv/zenimport-backend.git
cd zenimport-backend
git checkout v0.2.0
export ZENIMPORT_IMAGE=ghcr.io/vaslv/zenimport-backend@sha256:4887d92e74ecee5454a5c84bc15ff586b8879849ce53006f05b56e63eff48d9f
docker pull "$ZENIMPORT_IMAGE"
./scripts/init-server.sh
```

The helper creates a private `.env` without overwriting existing settings. Set
`ZENMONEY_TOKEN` on the server to the same ZenMoney data set as the source. Only
the generated backend `API_TOKEN` goes into the extension. Keep the chosen
`DATA_VOLUME` stable. Then start:

```sh
docker compose -f compose.server.yaml up -d --wait backend
curl --fail http://127.0.0.1:18792/health
```

Use the [HTTPS server guide](server-deployment.md). Port 18792 stays on loopback.
The proxy must permit 64 MiB raw ZIP uploads and sufficient response time (the
extension permits 120 seconds); ordinary API bodies remain limited to 2 MB.

## Upgrade an existing deployment

Finish capture, stop the service gracefully, and save a consistent snapshot using
[operations.md](operations.md). Preserve the named volume and delivery journal.
Update source to `v0.2.0`, change `ZENIMPORT_IMAGE` in the existing `.env` to the
new verified image reference, pull it, and recreate the service with
`docker compose -f compose.server.yaml up -d --wait backend`. Do not run bootstrap
over an existing configuration, change DATA_VOLUME, or delete volumes.

For an existing source-built local deployment, update source to `v0.2.0` and use
`docker compose up --build -d --wait backend`, retaining the existing `.env`/volume.
The publication task itself does not restart any production deployment.

## Move through the extension

Use Chrome extension 0.2.0. On the upgraded source: **Settings → Data migration →
Export data**. Save the private ZIP; the source stays paused. Configure the new
server address/API token, then **Import data before Synchronize**. The destination
must have no prior sync/history. Import sends no financial changes; the next
explicit Synchronize refreshes ZenMoney before delivery. Keep the source paused
or shut it down. [Full sequence and safety limits](popup-migration.md).

The ZIP is unencrypted financial history, excluding server tokens. Preserve the
entire archive privately. Resume the old source only for a cancelled move, and
create a new export after any further source processing. Never run both servers
or downgrade a paused server to an image that cannot enforce the migration hold.
