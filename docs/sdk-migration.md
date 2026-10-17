# Backend on SDK 0.6.0 and database schema 11

Completed **October 14, 2026**, independently of the ZenMoneyAPI library project.
ZenImport implementation: local commit `b3ad95b`.

## Changes

- Backend directly uses Client.read_diff/upsert and transactions builders from zenmoney_api. Removed the legacy namespace/HTTP adapter and old wheel.
- One Client per application, connection reuse, lifespan closure.
- Preserved preview/confirmation, durable UUIDs/intents, independent post-write reconciliation, stopping on unknown outcomes, and explicit retry preparation.
- Amounts/rates/coordinates are stored with Decimal; SQL/HTTP serialization never passes through float. Numeric fingerprints are stable across trailing zeros.
- Every SDK model field has an SQL projection or movement-category table; unknown fields/collections remain in full JSON/source archives.
- Added original transaction currencies, bank IDs, coordinates, reminders, and complete account/category/budget/recurring transaction settings.
- Removed the duplicate zen_entities mirror. Matching/queue verification reads structured tables with exact complete JSON.
- A complete received forceFetch collection removes absent records from the current mirror; ordinary incremental responses never clear it based on absence.

The extension API is preserved. New user forms for transfers/debts were outside this
refactor; SDK/storage are ready for a separate subsequent stage.

## Current data validation

Created a SQLite backup including WAL before changes. Migration was first tested
in Docker without networking on a separate user database copy:

| Data | Before and after migration |
| --- | ---: |
| All current ZenMoney entities | 32,608 |
| ZenMoney transactions, including deleted | 29,061 |
| ZenMoney accounts | 114 |
| Categories | 33 |
| Bank transactions | 554 |
| Matching groups | 499 |
| Creation jobs | 66 |

An original archived object was found for all 32,608 entities; zero lacked a source.
Exact values and previously unprojected fields were recovered. Data/hash comparison
confirmed unchanged bank transactions/imports/bindings, every group participant,
exclusion, category, rule, previously sent payload, queue identifier/state, and
**the entire archive**. Prepared jobs receive only missing SDK defaults while
retaining UUIDs. Integrity/FK checks passed. Reinitialization creates no new backup
and changes no data.

The local container was then rebuilt/recreated; automatic migration of the actual
volume succeeded. Repeated SQL comparison against its backup confirmed preservation
of all listed application data and archive contents. Container healthy, SDK 0.6.0,
schema 11; /api/state and /api/zenmoney/history return HTTP 200. The updated backend
performed an **actual read** /api/zenmoney/sync → ZenMoney: HTTP 200, synced/LIVE.
This check sent no financial changes.

## Automated checks

Complete `sh scripts/test-docker.sh`:

- 167 backend tests, including migration/rollback/idempotency, exact JSON/Decimal, all SDK fields, category arrays, authoritative snapshots, lifecycle, and HTTP.
- 19 bank parser tests.
- 70 extension/SPA browser scenarios.
- Real HTTP smoke and preservation of mappings/UUIDs after container recreation.

Tests received no live token. Lost response, partial write, and conflict scenarios
used a mock API without repeated writes. The independent live SDK matrix is in the
neighboring ZenMoneyAPI project; its results do not replace these application checks.

## Backups and restoration

First external local copy: `data/backups/pre-sdk-2026-10-14.sqlite3` (Git-ignored,
0600). Automatic premigration copy beside the production database in the LIVE volume:
`/data/zenimport.sqlite3.pre-v11-<UTC>-<suffix>.sqlite3`, also 0600.

To restore, stop the backend, return to the previous schema 10 image/checkout, and
restore the backup as /data/zenimport.sqlite3 in the corresponding volume. With the
database closed, remove only its old WAL/SHM sidecars. Do not run the legacy version
against schema 11; initialize rejects a newer database version. Never restore over
an open SQLite database or remove the volume with down -v.

Rehearse migration on a separate copy using scripts/verify_migration.py in Docker
without networking. The script outputs only counts and check results, never
transactions, account details, or tokens.

Migration does not guess lost precision without an original archive: it preserves
available values and sets needs_full_sync. The archive currently has no retention;
a current account backup therefore takes about 1.4 GiB.
