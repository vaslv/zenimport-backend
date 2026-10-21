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

Archive-only disaster restore and popup wiring are subsequent L10/L11 gates; these
endpoints do not implement restore or mode selection. They do not automatically
pause/retire another installation or authorize offline fallback.
