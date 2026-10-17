# ZenImport Backend

An independent Docker service that receives bank history from Chrome and adds new transactions to ZenMoney. Neither extension source nor an SDK checkout is required to build. Docker installs the public `zenmoney-python==0.7.0` wheel from PyPI with a pinned SHA256; no PyPI token is needed.

The backend and Chrome extension are separate projects; financial storage v6, mirroring/prediction, and durable delivery are implemented. At the user's direction, the new local backend started with a clean database on October 16, 2026. Current status, extension settings, and initial import limitations: [docs/local-deployment.md](docs/local-deployment.md). Live mobile acceptance is deferred; do not connect the old extension to the new protocol.

Plan: [docs/refactor/README.md](docs/refactor/README.md). Status: [docs/refactor/05-progress.md](docs/refactor/05-progress.md). Contract: [docs/sync-api-v1.md](docs/sync-api-v1.md). Legacy module extraction decisions: [docs/extraction-map.md](docs/extraction-map.md).

## Published server image

Backend 0.1.0 is available publicly for Linux AMD64 and ARM64:

```sh
docker pull ghcr.io/vaslv/zenimport-backend:0.1.0
```

[Verified digest, native CI results, and installation commands](docs/container-release-0.1.0.md).
For deployment, use that immutable digest and keep SQLite in the named volume.
When moving an existing installation, transfer its consistent database and delivery
journal before switching the extension; do not run old/new workers together.

## Local startup

Docker Engine and Compose are required. Host Python/Node installations are not needed.

```sh
# Only when .env does not exist:
docker compose --profile tools run --build --rm --no-deps --user "$(id -u):$(id -g)" init
# After reviewing the settings:
docker compose up --build -d --wait backend
```

The generator creates `.env` with mode 600, a random `API_TOKEN`, and explicit settings. It never overwrites an existing file or prints the token. `API_TOKEN` connects the extension; `ZENMONEY_TOKEN` belongs only on the server. The service can start without a ZenMoney token, but the target catalog and mirror refresh are unavailable. Compose reads `.env`; the application does not search for configuration files itself.

The service is available at `http://127.0.0.1:18792`, separately from the former port 18791. The backend token must contain at least 24 characters and no whitespace. `DATABASE_PATH` is an absolute path **inside the container**, default `/data/zenimport.sqlite3`. Data uses the separate named volume `DATA_VOLUME=zenimport-backend-data`; its name is stored in `.env` and does not depend on the launch directory. One process/one backend instance.

| Request | Response |
|---|---|
| GET `/health` without a token | 200 when storage is available; otherwise 503 |
| GET `/api/v1/info` with Bearer API_TOKEN | Version, request limits, connection status |
| Registration/settings/catalog/jobs/pages/complete/stop | Implemented in B3; [routes](docs/sync-api-v1.md), [rules](docs/capture.md) |

`not_configured` means the server has no ZenMoney token. With a configured token, `ready` is returned only after a successful SDK read no older than 300 seconds. The catalog and new sync commands refresh the mirror; startup only resumes previously authorized delivery/verification and creates no new jobs. Bank capture continues during ZenMoney outages; delivery waits for a fresh mirror. Prediction rules: [docs/mirror-predictions.md](docs/mirror-predictions.md). Delivery, uncertain outcomes, and recovery: [docs/delivery.md](docs/delivery.md). No standalone HTML pages, Swagger, or editor are served.

```sh
docker compose logs --tail 50 backend
docker compose down
```

An ordinary `down` preserves the named volume. After configuration changes, recreate the container with `up -d --force-recreate --wait backend`. Do not change the volume name during an ordinary update: that connects a different database.

## Server deployment

Image-only installation and HTTPS: [docs/server-deployment.md](docs/server-deployment.md). SDK pinning: [docs/sdk-dependency.md](docs/sdk-dependency.md).

Complete startup/update, snapshot, and restore instructions: [docs/operations.md](docs/operations.md). Actual platform validation: [docs/b6-validation.md](docs/b6-validation.md).

Transfer this project or its built image to the target machine; build for that machine's architecture. The same Dockerfile/Compose works locally and remotely. Settings are supplied at runtime; the image contains no secrets. Publishing an image to a registry is optional.

On a server, keep HTTP bound to loopback and configure your HTTPS reverse proxy to `127.0.0.1:18792`. The extension uses the external HTTPS address and a separate API_TOKEN. Never pass the ZenMoney token to the extension. Persistent local Docker storage and backups are required; multiple service instances/shared network SQLite volumes are unsupported.

The service does not mount the old application's volume: databases with another application_id/schema are rejected **before changes**. Conversion and storage policy: [docs/financial-storage.md](docs/financial-storage.md). A rehearsal on a real copy was performed separately (docs/migration-rehearsal.md); the user chose a clean database for the current deployment. Use a consistent SQLite backup, or stop the service and preserve the entire volume including WAL; copying only the main file of a running SQLite database is not a reliable backup.

## Checks

```sh
scripts/test-docker.sh
# Or both architectures sequentially:
scripts/test-platforms.sh
```

The script supplies a synthetic token and unique Compose project/volume names, does not read `.env`, publishes no port, and never uses the production database. It checks actual architecture/UID/read-only root, the configuration image, network-isolated unit/SDK tests, real HTTP on an internal test network, job recovery, SIGKILL, graceful shutdown during delivery, and snapshot restoration without repeat writes. The installed pinned SDK is tested using fake HTTP, without a neighboring library. Only the script's own test environment is removed.

The test override requires Compose support for `!reset`. `scripts/test-contracts.sh` can also run separately. ARM64 and AMD64 actually passed 347 tests each and the container scenarios; AMD64 ran under OrbStack emulation on an ARM64 host. Details and boundaries: docs/b6-validation.md.

## Structure

- `backend/app.py`, `http.py`: lifecycle, API, authentication, body limits, and errors without input data.
- `backend/snapshot.py`: validated SQLite export/restore into a new file, never overwriting the source or an existing destination.
- `backend/config.py`: settings and safe `.env` creation.
- `backend/storage.py`, `schema_v*.sql`, `records.py`: versioned database, constraints, financial entities, identities, and record versions.
- `backend/accounts.py`, `capture_policy.py`, `capture.py`, `sync_api.py`: discovery/settings, data policy, jobs, pages, and HTTP routes.
- `backend/mirror.py`, `predictions.py`: read-only SDK diff/cursor/deletions and predictions from current confirmed transactions.
- `backend/delivery.py`: complete-version authorization, one worker, viewed=false creation, UUID verification, and recovery without blind retries.
- `backend/migrate_legacy.py`, `legacy_archive.py`: explicit legacy v12 conversion into a separate database with a verifiable archive.
- `backend/sync_contracts.py`: v1 models carried over from B0.
- `requirements.sdk.lock.txt`: public PyPI SDK version/SHA256; `requirements*.lock.txt`: exact dependencies.
- `tests/`, `fixtures/sync-v1/`: independent and container checks.
- `docs/`: contract, stages, and extracted code provenance.

Extracted from ZenImport `f3b2838` by a local Git copy without hardlinks; initially no remotes. The complete prior history is retained. Local commits are allowed; pushing/tags/publication require separate permission.

Read-only report of potentially extra ZenMoney transactions: GET `/api/v1/connections/{id}/discrepancies`. The page belongs to the Chrome extension; comparison scope, format, and limitations: [docs/discrepancies.md](docs/discrepancies.md).

Isolated server migration rehearsal and explicitly invoked separate test account acceptance: [docs/migration-lab.md](docs/migration-lab.md).

Remote deployment from a prebuilt image: [docs/server-deployment.md](docs/server-deployment.md).
Source repository: https://github.com/vaslv/zenimport-backend, MIT.
SDK → backend → Chrome publication sequence: [docs/publication.md](docs/publication.md).
