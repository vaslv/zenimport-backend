# Portable archive backups

Archive backup controls are independent of live peer migration. They produce
portable format-2 ZIP files without inventing a destination or a handoff receipt.
Financial schema 1 is unchanged. Physical SQLite schema 9 adds only private
`archive_records` and `archive_chunks`; these controls never become portable data.
An owned schema-8 installation upgrades transactionally with a private pre-upgrade
backup, preserving physical authority and financial history. Format-1 archives
from schema 6 through the current schema remain explicitly convertible/restorable.

All endpoints below require the server bearer token and return `Cache-Control:
no-store`. The prefix is `/api/v1/migration/archives`. Epochs, sizes and counts in
JSON use canonical decimal strings; URL chunk indexes are integers.

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/backups` | Reserve a snapshot using `id`, `installation_id`, `expected_epoch` |
| GET | `/{id}` | Read persisted phase, identity, checksums, size and chunk count |
| GET | `/{id}/chunks/{number}` | Download one verified 256 KiB chunk after readiness |
| POST | `/{id}/retry` | Retry interrupted/blocked work; body `{}` |
| POST | `/{id}/cancel` | Release only that unfinished backup hold; body `{}` |
| POST | `/{id}/acknowledge` | Acknowledge a verified download with `sha256` |
| POST | `/{id}/clean` | Delete one cached chunk after acknowledgement/cancel; body `{}` |

Before reservation, finish/stop bank capture (the explicit portable quiesce
endpoint is available). The server must have a verified owner mirror and no live
migration/import hold. New holds reserve both fence increments before pausing.
A backup pauses all clients of that physical server while it is being built.
Workers preserve an interrupted financial send as uncertain; backup work never
sends or retries a financial SDK request.

The background owner exports the held journal, validates the complete archive and
financial graph, writes bounded private chunks, and rechecks durable bytes. Only
then does it atomically mark the backup ready and release its own hold. The source
remains authoritative and unchanged. The ready archive is a snapshot, not evidence
that this source was permanently retired.

One non-cleaned archive cache is allowed. Exact begin retries return the same
persisted outcome. An interrupted or failed build remains paused, visible and
retryable/cancellable. A process restart rebuilds only unpublished chunks. Scratch
paths are scoped to the physical installation and swept under the cross-process
writer lock, even after cancellation. Symlinks are unlinked rather than followed;
foreign installation paths are retained.

After downloading all chunks, verify aggregate SHA256 against status and save the
ZIP outside the server. Only then acknowledge its checksum. Cleaning cached chunks
is explicit and bounded, preserves the archive audit, and never removes financial
history. An acknowledged backup cannot be downloaded again after cleanup.

## Archive-only disaster recovery

Use this distinct path only after explicitly retiring every former writer. An
archive cannot prove retirement or preserve events recorded after its snapshot.
The destination must be unused, with no started capture/history, prior import or
activated writer. Existing histories are never merged or overwritten.

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/recoveries` | Reserve an empty destination after fresh owner verification |
| PUT | `/{id}/chunks/{number}` | Upload immutable bytes (`data` base64, `sha256`) |
| POST | `/{id}/finalize` | Queue complete graph validation and a fresh read-only preview |
| GET | `/{id}/preview` | Read checksum/cursor/time-bound reconciliation counters |
| POST | `/{id}/activate` | Accept that exact `preview_id` with `accept_summary: true` |

The recovery request contains `id`, `installation_id`, `expected_epoch`,
`dataset_id`, `archive_sha256`, `archive_size`, ordered `owner_ids`,
`former_writers_retired: true`, and
`accept_post_snapshot_evidence_loss: true`. These declarations do not contact or
resume the former source. They are explicit disaster-recovery consent, not peer
handoff receipts. All JSON byte sizes/epochs are decimal strings.

The destination holds globally before upload. Exact chunk retries are idempotent;
conflicting bytes and incomplete hashes fail. The background owner validates the
entire graph, then uses a fresh full SDK response in a disposable preview database.
The active destination remains empty. Summary counters describe missing bank
movements, extra ZenMoney account movements, and unchecked accounts; permanent
tombstones exclude reappearing deleted transactions. They are reconciliation
information, never an automatic instruction to delete or create a transaction.

The summary hashes the actual SDK response bytes and records read time before
projection/report work. It expires after five minutes. Activation requires the
exact accepted preview, a fresh full SDK owner check, unchanged protected credential
context, the same archive/manifest and an eligible held destination. The final
transaction checks age before and after copying; expiry or any SQL fault rolls
back imported facts, triggers and authority together. Activation preserves the
physical installation, adopts financial dataset lineage, advances its epoch and
requires explicit synchronization before delivery workers may send anything.
No peer receipt is invented. A lost success response can only replay the permanent
activated outcome; cancelled/cleaned attempts cannot masquerade as activation.

Cancel releases only that recovery's own hold before activation and never resumes
a former installation. Explicit cleanup removes only private uploaded chunks.
Successful activation remains a permanent outcome even after acknowledged file
cleanup. It does not block a new backup after the user explicitly synchronizes.
Process interruptions retain upload/preview state; scratch cleanup is installation
scoped. Retry never automatically sends financial SDK mutations.

Popup wiring remains an L11 gate. Neither backup nor archive restore selects a
browser storage mode or authorizes automatic offline fallback.

## Withdrawing a rejected recovery request

Before freezing a browser route for remote recovery, discover
`GET /api/v1/migration/archives/capabilities`. This authenticated, read-only reply
contains `archive_control_version: "1"` and `recovery_withdrawal: true`. Older
servers must be upgraded before this recovery path is offered.

`POST /api/v1/migration/archives/recoveries/withdraw` takes the exact full recovery
request, including its original ID, expected epoch, archive digest and consent.
It permanently withdraws that command. If reservation never happened, it stores
an immutable terminal tombstone without changing any participant, history, epoch,
foreign hold or chunk. An existing reservation can cancel only its own hold,
atomically. Activation is permanent and cannot be withdrawn. Conflicting request
content or a different archive kind under the same ID is rejected.

The reply has exactly `withdrawal_version: "1"`, `decision: "withdrawn"`, `id`,
`request_sha256`, `installation_id`, `dataset_id` and `participant_epoch`.
The digest covers the canonical full recovery request. Dataset and epoch describe
server state observed with the permanent withdrawal decision. The browser checks
this proof and a fresh authenticated identity against its original selected
physical server and dataset before releasing its local route hold. A wrong owner
in the rejected archive must not prevent restoration of that original route;
withdrawal grants no imported writer. An unavailable or changed server leaves the
browser paused, with the original immutable request retained for retry.

A late begin returns the saved cancelled/cleaned result. Retrying cancelled work
cannot restart it. Upload, finalization and activation cannot bypass the terminal
record. Cache cleanup retains the request audit. Lost withdrawal replies can be
retried without releasing a newer foreign operation. Ordinary capture APIs and
live peer migration do not gain an offline escape or a resume shortcut.

## Cancelling a backup whose begin outcome is unknown

Before creating a durable client backup request, check authenticated
`GET /api/v1/migration/archives/backups/capabilities`. The exact response is
`{"archive_control_version":"1","backup_withdrawal":true}`. This separate endpoint
preserves the existing recovery capabilities contract unchanged.

`POST /api/v1/migration/archives/backups/withdraw` accepts the same exact
`BackupRequest` as begin: `id`, `installation_id`, `expected_epoch`. Its permanent
request audit prevents a delayed begin from creating a new hold after withdrawal.
A never-accepted request records a cleaned tombstone without changing authority.
An owned building/blocked backup releases only its own hold atomically. A ready
snapshot has already released its hold; withdrawal discards only that snapshot
cache, preserving every current installation/epoch/foreign operation field.

The response uses the version-1 withdrawal proof: `withdrawal_version`, `decision`,
`id`, `request_sha256`, `installation_id`, `dataset_id`, `participant_epoch`.
`request_sha256` hashes the exact canonical backup request. It proves withdrawal,
not download, migration, activation, retirement or peer handoff. Existing archive
request/hash/manifest/acknowledgement evidence remains immutable. No acknowledgement
is fabricated to discard a ready snapshot. Bounded `/clean` calls remove its cache;
financial rows remain untouched. Conflicting request bodies, wrong physical
installations and recovery IDs are rejected. Repeated exact withdrawals and begins
cannot restart cancelled work.
