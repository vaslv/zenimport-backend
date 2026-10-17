# ZenMoney storage — schema 12

Schema 12 extends API projections with local transaction type definitions and
verifiable delivery intents: `bank_operation_drafts`, `zen_operation_drafts`,
`zen_operation_sends`. All previous tables remain.
Details: [operation-types.md](operation-types.md).

Updated October 14, 2026. This mirrors the public diff API, not the service's internal
database. Structures follow SDK 0.6.0 models; unknown fields remain in complete entity
JSON, unknown collections in the original response.

## Entities and relationships

| API | Table | Projections |
| --- | --- | --- |
| Instrument | zen_instrument | Title, code, symbol, exact rate |
| Company / Country | zen_company / zen_country | Titles, website, country |
| User | zen_user | Login, parent, base currency |
| Account | zen_account | Type, owner, bank, currency, balance/start balance/limit, role, syncID, archive/private, correction/SMS settings, loan/deposit parameters |
| Tag | zen_tag | Hierarchy, title, icon/picture/color, income/outcome and budget flags, archive/staticId |
| Merchant | zen_merchant | Title, owner, MCC |
| Budget | zen_budget | user/date/tag key, both amounts, locks/forecast |
| Reminder | zen_reminder | Both sides/currencies/amounts, merchant/payee/comment, recurrence, step/points, dates, notify |
| ReminderMarker | zen_reminder_marker | Both sides/currencies/amounts, reminder, date, state/notify/forecast |
| Transaction | zen_transaction | Both sides/currencies/amounts, original amounts **and currencies**, created/changed/deleted/hold, merchant/payee/originalPayee/comment, MCC, reminderMarker, coordinates, viewed, QR/source, bank IDs |
| Movement.tag | zen_transaction_tags / zen_reminder_tags / zen_reminder_marker_tags | Owner, category, position |
| deletion | zen_deletions | Event, run_id, kind/entity_id, complete JSON |
| Complete diff | zen_sync_runs | Requested/received cursor, time, origin, unchanged payload |
| Migration | zen_migrations | Version, time, recovered record counts |

The executable field matrix is FIELDS in backend/zenstore.py. A test compares it with
SDK model fields so new library fields cannot be silently omitted. Budget.id is an
internal exact JSON representation of the composite API key; no invented id is added
to the original Budget object. Nullable tag is retained.

zen_entities was removed: current data is read directly from entity tables. Bank
tables, many-to-many matching groups, categories/rules, and delivery queues remain
the application layer; their identifiers/states are preserved.

## Types and precision

SQL identifiers are TEXT, including numeric system IDs; complete JSON preserves the
original API type. Amounts, rates, percentages, and coordinates use Decimal in TEXT,
never SQLite REAL. Timestamp/integer/boolean use INTEGER; nullable remains NULL.
syncID/points are JSON arrays; movement categories use separate tables.

Every internal JSON boundary uses exact serialization. HTTP endpoints return Response
directly: FastAPI never converts Decimal to float before responding. JSON amounts
remain numbers; history additionally returns exact textual _amounts. JavaScript Number
has its own precision limits, so these strings support display/external clients.

References between remote catalogs are logical: diff can be partial and objects can
be deleted/archived. A missing category/account never destroys a whole transaction.
Internal Movement→tags tables use FK ON DELETE CASCADE; indexes cover dates,
accounts, payee, merchant, currencies/owners, and categories.

## Synchronization

One transaction persists raw diff, projections/categories, deletions, and cursor.
Soft-deleted Transaction remains stored but is excluded from ordinary history/matching.
Hard deletion removes the current record/categories while preserving archives.
Explicit forceFetch with a received collection also removes records absent from the
full snapshot. Incremental responses/omitted collections never do this. Unknown
deletions are journaled. Ambiguous Budget deletions are never guessed: the public
SDK clears budgets through amounts/locks, not deletion by UUID.

GET /api/zenmoney/history provides limit/offset, period, account, include_deleted.
/api/state contains the first 100 records and summary. Minimum/maximum dates and
coverage_verified=false never prove complete service history.

## Migration and operations

Before migration, initialize creates a mode-0600 SQLite backup beside the database:
*.pre-v11-<UTC>-<suffix>.sqlite3, including WAL. It then adds columns/relationships
in one transaction; current objects are rebuilt from **their own** archived responses
without restoring already hard-deleted records. Parse one archived response at a time.
Missing originals preserve the current object and set needs_full_sync. Cursor survives.

Unsent prepared/queued payloads receive SDK defaults with their original UUID.
Uncertain/sent attempts remain unchanged. Any error prevents committing the new
schema version; the backup remains for diagnostics/recovery.

SQLite WAL, one worker, separate DEMO/LIVE volumes. At this historical stage, sync
network calls still occur inside transactions and block other changes. Archives grow
on each read; retention/PostgreSQL migration are outside this change. The backup is
in the same volume; protect against disk loss with a separate external copy.

[Current-data validation report](sdk-migration.md).
