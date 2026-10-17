# Backend startup, updates, and recovery

One Dockerfile/Compose for Linux ARM64 and AMD64. The target machine needs Docker
Engine/Compose, not separate Python/Node installations or neighboring SDK/Chrome
checkouts. Everything below concerns the new ZenImportBackend. Commands use default
DATABASE_PATH=/data/zenimport.sqlite3; if customized, replace it in every snapshot/
restore command. Do not mount the legacy ZenImport production volume here; migration
of real data remains M1.

## First startup

From the project directory:

```sh
docker compose --profile tools run --build --rm --no-deps --user "$(id -u):$(id -g)" init
```

Creates `.env` with mode 600, a random API_TOKEN, and a separate persistent DATA_VOLUME.
Existing files are not replaced. Set ZENMONEY_TOKEN on the server; the extension uses
API_TOKEN. Never put secrets in Git, images, screenshots, or command arguments.
Do not send the ZenMoney token to Chrome.

```sh
docker compose up --build -d --wait backend
docker compose ps
docker compose logs --tail 50 backend
```

Health is available at `http://127.0.0.1:18792/health`. It checks storage, not completion
of financial synchronization. `/api/v1/info` requires Bearer API_TOKEN. Use the
independent ZenImport Chrome extension with contract v1; the old popup is incompatible.
Chrome 0.1.8 synchronization was tested against actual VietinBank; results are in the
[acceptance log](refactor/05-progress.md).

Docker defaults to the native architecture. Build separately with
`docker build --platform linux/arm64 --target runtime .` or `linux/amd64`.
Consider image architecture when transferring it to another machine. A registry/push
is optional: build on the target machine instead. Emulation helps validation but
does not replace measurements on native servers.

The service uses UID/GID 10001, read-only root, writable `/data`, temporary `/tmp`,
no capabilities, and no privilege escalation. Use a local Docker volume. Arbitrary
bind mounts require permissions prepared for UID 10001; shared network SQLite volumes
and multiple backend instances are unsupported.

## Server

Use the same commands, `.env`, and persistent container paths. External access goes
through your HTTPS reverse proxy to loopback `127.0.0.1:18792`. It must forward
Authorization, never log bodies/tokens, accept up to 2 MB, and allow initial ZenMoney
reads to take longer than ordinary GET requests. The backend does not issue TLS
certificates. Do not expose its HTTP port directly to the internet. Validate HTTPS,
permissions, and actual networking on the target server; local container tests do
not validate your external TLS.

Keep DATA_VOLUME stable in `.env`: another directory/Compose project must not
unexpectedly attach an empty database. Do not change it during ordinary updates.
`docker compose config` outputs interpolated secrets; never publish its output.

## Stopping and updating

```sh
docker compose stop backend
```

SIGTERM stops new attempts; the server finishes the current exchange. Compose allows
30 seconds before possible forced termination. Intent is persisted before networking;
after a crash, the result is checked using the old UUID without blind retries.
Depending on signal handling, exit code may be 0 or 143; what matters is a graceful
shutdown log and absence of forced SIGKILL.

After stopping, save a snapshot as described below, then update the code/image:

```sh
docker compose up --build -d --wait backend
```

After `.env` changes, use `up -d --force-recreate --wait backend`. Ordinary
`docker compose down` preserves the volume. **Never use `down --volumes` in production.**
Automatic own-schema upgrades create private pre-vN backups. Legacy v12 conversion
requires the separate new-file command in [financial-storage.md](financial-storage.md).

## Snapshot and restoration

Stop the backend first for a consistent recovery point. The exporter uses SQLite
backup including committed WAL, validates ownership/schema/FKs/integrity, and publishes
a mode-600 file. Neither source nor existing destination is overwritten. Every new
snapshot needs a new filename.

```sh
docker compose run --rm --no-deps backend python -m backend.snapshot export \
  --source /data/zenimport.sqlite3 --destination /data/backup-before-update.sqlite3
```

A snapshot in the same volume does not protect against disk loss. Keep another copy
in a private host directory. For example, after `up --no-start` creates the container
without starting the application, use Compose cp:

```sh
mkdir -m 700 backups
docker compose up --no-start backend
docker compose cp backend:/data/backup-before-update.sqlite3 ./backups/backup.sqlite3
chmod 600 ./backups/backup.sqlite3
```

`up --no-start` creates no financial records. Never substitute copying the main file
of running SQLite for a consistent snapshot or remove WAL/SHM to repair it.
`backend.snapshot export` can also take online copies, but the worker may send more
transactions afterward. Restoring an older snapshot loses later attempt history;
automatic duplicate prevention cannot be guaranteed during that lost period. Prefer
the latest complete copy after stopping; never reset delivery journals for unknown states.

Restore into a **new** volume, retaining the old one as the source:

```sh
DATA_VOLUME=zenimport-backend-restored docker compose run --rm --no-deps \
  --user 0:0 --cap-add CHOWN --cap-add DAC_OVERRIDE \
  --volume "$PWD/backups:/input:ro" backend sh -c \
  'python -m backend.snapshot restore --source /input/backup.sqlite3 --destination /data/zenimport.sqlite3 && chown 10001:10001 /data/zenimport.sqlite3'
```

The one-time helper reads the private copy as root and returns restored-file ownership
to UID/GID 10001; the service itself remains non-root without these capabilities.
This also permits mode-700 host directories and mode-600 snapshots on ordinary Linux
without opening them to everyone.

Before selecting a new name, ensure it identifies a separate empty volume. Restore
refuses to replace existing files. This helper transfers the current own schema;
foreign/unsupported schemas are rejected. After validation, set the new DATA_VOLUME
in `.env` and run `up -d --force-recreate --wait backend`. Never run two backends
against the same ZenMoney account simultaneously. Returning to the old volume is
safe only if the newer one has not sent anything: financial journals are not disposable
caches. Never open a new-schema snapshot with an older unsupported image; retain a
matching image/source alongside each snapshot.

## Synchronization results

JobView separates capture and delivery. Delivery `complete` means complete capture
and processing of every record. `blocked`/`uncertain` require further verification,
not repeat writes. Synchronize repeats reads of old UUIDs; transactions deleted in
ZenMoney are not recreated. Details: [delivery.md](delivery.md). Errors must not contain
transactions/tokens; health 503 or rejection of a foreign schema calls for checking
path/volume/permissions, not replacing it with a new database.

## Automated checks

```sh
scripts/test-docker.sh linux/arm64
scripts/test-docker.sh linux/amd64
# Or both sequentially:
scripts/test-platforms.sh
```

Docker must support the selected architecture natively or through emulation, and
Compose must support `!reset`. Unsupported architectures fail rather than skip.
The suite supplies its own fake tokens/project/volume, never reads `.env`, and
publishes no ports. Unit/SDK tests have no network; HTTP scenarios use an isolated
internal network. The SDK MockTransport app factory exists only in the test image,
not runtime; .env cannot configure a fake API.

Checks cover actual Python architecture, UID/read-only root, HTTP/auth, repeated
commands/pages, integrity/preservation after recreation, actual worker delivery,
SIGKILL between fake API commit and response, recovery without repeat writes,
graceful shutdown with a write in flight, snapshot, and a new database file.
Cleanup removes only the unique test project's resources. Actual results/limitations
are in the [log](refactor/05-progress.md). New Chrome integration (E5), real data (M1),
and bank/mobile ZenMoney (M3) are checked separately.

Official Docker references: [platform](https://docs.docker.com/reference/compose-file/services/#platform),
[multiplatform builds and emulation](https://docs.docker.com/build/building/multi-platform/),
[stop_grace_period](https://docs.docker.com/reference/compose-file/services/#stop_grace_period).
