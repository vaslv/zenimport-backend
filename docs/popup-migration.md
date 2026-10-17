# Move a backend through the Chrome popup (0.2.0)

Use backend and extension 0.2.0 or newer, with the same supported storage schema.
This feature is prepared locally; the published 0.1.0 image and 0.1.9 extension do
not contain it. Publishing new artifacts requires separate authorization.

## User flow

1. In the extension connected to the old server, open **Settings → Data migration**
   and click **Export data**. This stops the extension's bank capture. Another
   active capture must be stopped by its owner. An in-flight request or delivery
   returns a retryable busy result; retry after it completes.
2. Save `zenimport-migration.zip` and check that the download completed. The source
   stays paused, including after process restart or popup closure. Capture, catalog
   mutation, and delivery are blocked; health, info, migration status, repeated
   export, and explicit resume remain available.
3. Change the address and backend token in **Connection**. Connect to the new
   server, configured with a ZenMoney token for the same financial data set.
   Discovering accounts or reading the catalog does not disable import.
4. Before Synchronize, click **Import data** and select the ZIP. The server checks
   it and verifies the current ZenMoney user set using a read-only request. Import
   transfers the full journal in one SQLite backup transaction. It sends no
   financial changes. The worker remains on hold until an explicit synchronization.
5. After **Data restored**, click **Synchronize**. Current ZenMoney data is read
   before delivery. Existing UUIDs, deletions, and uncertain attempts are retained;
   uncertain writes are verified, never blindly retried. Leave the old server
   paused or shut it down.

If the move is cancelled, **Resume this server** explicitly removes the source
pause. Do not resume it after the destination has started operating. If the source
continues processing, make a new export before moving later: the older file is
only an older recovery point. There is no distributed lock between disconnected
servers. Do not run both against the same financial history or downgrade an
exported source to an image that does not understand persistent migration pauses.

## Archive and safety

The ZIP has exactly `manifest.json` and `database.sqlite3`. The manifest includes
format/schema/service versions, time, source instance UUID, mirrored ZenMoney user
IDs, database size and SHA256. The SQLite snapshot contains bank history and
identities, mappings, coverage/jobs, prediction evidence, delivery UUIDs/attempts,
processing receipts/actual targets, deletion tombstones, and exclusions. The full
relational snapshot preserves dependencies. Server API/ZenMoney tokens and `.env`
are not included; configure credentials separately on the destination.

The archive is **private and unencrypted** financial data. Transfer/store it
privately; do not attach it to public issues. SHA256 detects corruption, not
malicious modification. Only import trusted archives. Temporary private server
files are removed after requests. The user manages the downloaded file.

Popup closure may interrupt a transfer. Reopen settings to check durable server
state before trying again. Repeating the identical completed archive is idempotent
until the destination's first explicit sync. Another archive is rejected after a
successful import. There is no merge with existing financial histories.

- The server rejects import after the first successfully started job, including
  empty, failed, or cancelled jobs. Bank records or a prior import also block it.
  Previously discovered account settings and disposable mirror cache may be
  replaced on a destination that has never synchronized.
- Before application: exact ZIP members, sizes/CRC/checksum, ownership/schema,
  integrity/FKs, instance identity, and ZenMoney user-set equality are checked.
  Different tokens for the same verified user set are allowed. The imported
  mirror is reset for a full current read. Validation failures leave target
  history untouched; identity verification may refresh its disposable cache.
- ZIP/request limit: **64 MiB**. Unpacked database limit: **256 MiB**. Larger
  databases use the stopped-service snapshot procedure in [operations.md](operations.md).
  Archive member names never become extraction paths. SQL dumps are not executed.
- Ordinary API bodies retain the 2,000,000-byte limit. Only authenticated
  `POST /api/v1/migration/import` accepts a raw ZIP up to 64 MiB. Configure the HTTPS
  proxy for this size and enough upload/export time (the extension allows 120s).
  Never log Authorization or request bodies.
- No new Chrome/downloads permission: the trusted popup downloads a Blob and sends
  the selected File only to the already authorized server, with cookies omitted
  and redirects rejected. Opening settings never starts a transfer or a job.

## API additions, v1

- `GET /api/v1/migration/status`: `format_version`, `can_import`, `paused`,
  `awaiting_sync`, `imported_sha256`, `max_archive_bytes`.
- `POST /api/v1/migration/export`: ZIP attachment; persistent pause before snapshot.
- `POST /api/v1/migration/import`: raw `application/zip`, no multipart/base64;
  returns `restored`, `already_imported`, `sha256`.
- `POST /api/v1/migration/resume`: explicitly cancel the source pause.

All routes require Bearer API_TOKEN and return `Cache-Control: no-store`. Existing
redacted errors add `migration_busy`, `migration_paused`, `migration_not_empty`,
`migration_invalid`, `migration_wrong_account`, `migration_incomplete`, alongside
`payload_too_large` and ordinary authentication/availability failures.
