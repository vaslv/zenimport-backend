# B6 — independent backend validation

October 16, 2026. Check/snapshot implementation: **c6a5c9f**.
Ran `scripts/test-platforms.sh` on Docker Engine 29.4.0, Compose 5.1.2,
CPython 3.12.15. The script sequentially ran the same complete suite for two platforms:

| Platform | Actual execution | Result |
|---|---|---|
| linux/arm64 | Linux/aarch64, native ISA in an OrbStack Linux VM | **347 passed**, HTTP/lifecycle/snapshot passed |
| linux/amd64 | Linux/x86_64, emulation on an ARM64 OrbStack machine | **347 passed**, HTTP/lifecycle/snapshot passed |

Python/containers actually ran on both architectures; this was not merely a build.
No native physical AMD64 server was used. No images/registry were published.
The existing Starlette httpx TestClient warning remains; dependency changes were unnecessary.

## Validated behavior

- Runtime/config/tests build from their own Docker context; SDK 0.6.0 comes from vendor with SHA256 verification. No neighboring SDK/Chrome source is used.
- Running Python reports the expected architecture; UID/GID 10001, read-only root filesystem, no `.env` in the image. Each platform has its own disposable Compose project/volume names; no port is published.
- All 340 prior B1–B5 tests and seven new snapshot checks: committed WAL, private file, identity preservation, overwrite/foreign database rejection, publication failure/race, CLI export/restore. SDK/unit tests use network=none.
- Real HTTP/auth, partial job acceptance/resumption, repeated command/page, and backend recreation preserving financial data and delivery authorization.
- A separate test-only app factory runs the **ordinary backend and lifespan worker** through Uvicorn. The actual SDK uses a persistent fake remote in MockTransport; the scenario uses HTTP on an internal Docker network without contacting ZenMoney.
- The first unreviewed write is confirmed. During the second, the fake API commits the write and holds its response. The container receives SIGKILL. On restart, the worker confirms the original UUID by reading; no repeat upsert occurs.
- The third delivery also remains unfinished; SIGTERM initiates shutdown. Only after shutdown begins does the test release the response. The worker completes the current request, the server logs Application shutdown complete, and exits with **143** on both platforms. After restart, all three records are confirmed without new upserts.
- After stopping, a SQLite backup is saved, validated, and restored **into a new file**. The recreated server uses the restored database: the original instance_id, every UUID, integrity, viewed=false, and exactly the three original records.
- Both platform runs remove only their own test containers, networks, images, and volumes. The production server/volume are unchanged.

## Artifacts

- `scripts/test-docker.sh [linux/arm64|linux/amd64]`: one complete run; defaults to the Docker server architecture.
- `scripts/test-platforms.sh`: ARM64 then AMD64, without skipping unsupported platforms or publishing images.
- `backend/snapshot.py`: consistent export/restore into a new destination, absolute paths, own current schema/integrity validation, mode 600, and atomic no-replacement publication even during races. It is not a legacy migrator.
- [operations.md](operations.md): local/remote startup, HTTPS boundary, stop/update, snapshot/restore into a new volume, and results.

## Remaining work

The independent B6 portion is complete. Overall integration **E5** from the original
B6 acceptance criteria was not yet performed: the new E1–E4 Chrome/connector had
not been extracted. Next: **E1**. After E1–E4, run E5 with fake ZenMoney,
then M1–M3 before LIVE cutover.

The real API, bank, production database, external HTTPS on the target server, and
mobile New tab were not checked. viewed=false was verified in SDK payloads; this
does not prove its display in the mobile client. A snapshot restores the journal to
its creation time; restoring an old copy cannot guarantee preservation of later
attempts or absence of duplicates during the lost period.
