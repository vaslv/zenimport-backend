# Backend 0.2.1 — release preparation

Published on October 19, 2026 for Linux AMD64 and ARM64.

- Image: `ghcr.io/vaslv/zenimport-backend:0.2.1`
- Immutable reference: `ghcr.io/vaslv/zenimport-backend@sha256:f76f2e3a8dc1b79041f694f16764b673510279e0c77d8ec2529155dd3a532c79`
- Source/tag v0.2.1: `463f12ca1504d5768cd50ef676c627987b6cf383`
- [Source CI](https://github.com/vaslv/zenimport-backend/actions/runs/39260257528)
- [Publication and native immutable-image checks](https://github.com/vaslv/zenimport-backend/actions/runs/39260287161)

All jobs passed. Anonymous registry access verified the manifest digest and both
architectures. A separate local ARM64 pull verified image-only bootstrap, HTTP,
SDK, UID, persistent volume and source revision on a disposable database.

## Changes

See [CHANGELOG](../CHANGELOG.md). Pair this release with Chrome 0.2.1. It adds
BIDV VND capture, automatic named connections, schema 7, transfer-link recovery,
and a two-sided discrepancy report independent of delivery. Existing transaction
edits and deletions in ZenMoney remain final. Recovery may establish local links;
it does not itself create or edit remote transfers. Uniqueness checks remain in
place when later connections expose additional transfer sides.

## Upgrade an existing server

1. Finish or stop browser capture. Save a consistent database backup and private
   server configuration; do not copy a running SQLite file without its WAL.
2. For this published release, verify successful native CI and
   published-image acceptance on both architectures. Record the published digest
   and use that immutable reference in `ZENIMPORT_IMAGE`.
3. Stop the old backend, retain the same `DATA_VOLUME` and credentials, and replace
   only the image using `compose.server.yaml`. Never run two workers against copies
   of the same live journal. Schema 6 upgrades automatically to 7 with a backup.
4. Check health, startup logs and saved account mappings. Update Chrome to 0.2.1
   in the same unpacked installation directory, then reload the extension.
5. Synchronize and inspect discrepancies. A delivery error does not prevent a
   report when the bank interval and fresh ZenMoney mirror are available.

For rollback, stop the new worker and restore the pre-upgrade backup with the
previous image; do not run 0.2.0 on a schema-7 database. Reconcile any remote writes
made after the snapshot before resuming delivery from an older journal.

For a server move use [popup migration](popup-migration.md), upgrading both servers
first and importing before the destination's first sync. An empty database cannot
recover knowledge of transactions previously deleted in ZenMoney. Preserve the
journal rather than relying on amount/date matching to replace it.

## Local verification

On October 19, local ARM64 and emulated AMD64 each passed 543 tests, HTTP/auth,
persistent-volume recreation, SIGKILL recovery, graceful shutdown and snapshot
restoration. Image-only deployment checks passed on both architectures, including
private bootstrap, no overwrite, SDK version, non-root UID and OCI revision.
All automated checks use synthetic data, fake ZenMoney and disposable Docker
volumes. Local AMD64 runs use emulation; publication CI uses native runners.
Previous live transfer-recovery acceptance is recorded separately on October 19
in [the progress log](refactor/05-progress.md); it is not a new live-bank test here.

## Publication after separate authorization

Push the reviewed commit, run the manual `publish-image.yml` workflow from main,
and wait for source CI, multi-architecture publication and immutable-image
acceptance. Existing version tags in the registry must not be overwritten.
Record the resulting digest and source revision in this document. Git tags require
explicit authorization. Publish the extension ZIP only after backend acceptance.
