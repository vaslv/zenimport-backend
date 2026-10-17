# Financial storage — B2

The current independent database version is **5**, `application_id=0x5A494231`;
the baseline B2 financial schema is version 2. B3 settings/jobs: [capture.md](capture.md).
B4 mirror/predictions: [mirror-predictions.md](mirror-predictions.md).
B5 delivery: [delivery.md](delivery.md). Legacy ZenImport version **12** belongs to
a different version sequence. Runtime opens only its own database. A new empty
database is created automatically; own B1/v1, B2/v2, B3/v3, and B4/v4 upgrade
transactionally with private SQLite backups, including WAL. Foreign/legacy/future
schemas are not modified at startup.

## Data and constraints

| Group | Tables and purpose |
|---|---|
| Connections/accounts | `connections`, `bank_accounts`, `connection_accounts`: access method separate from the canonical bank account |
| Settings | Version/selection/start date in `bank_accounts`; previous mappings in `account_bindings` |
| Bank history | `bank_transactions`, `bank_aliases`, `bank_versions`: persistent UUID, source fields, aliases, immutable versions |
| History capture | `capture_jobs`, `capture_intervals`, `capture_pages`, `coverage`: B3 foundation; completeness is not inferred from the last transaction date |
| Permanent journal | `bank_processing`, `processing_receipts`: processing survives current mirror state and local link absence |
| Delivery | `deliveries`, `delivery_attempts`: persistent ZenMoney UUID, payload, unknown outcome, attempt history, `needs_rebuild` |
| Mirror | `zen_sync_runs`, `zen_entities`, `zen_movements`, `zen_tags`: original JSON and exact financial projections of all SDK kinds |
| Delivery authorization/planning | `delivery_authorizations`, `delivery_schedule`: verified complete bank version/job/target and durable verification delay |
| Mirror state/prediction | `zen_mirror_state`, `category_payments`, `category_predictions`: cursor/source/freshness and recomputable independent payments/predictions |
| Deletions | `zen_deletions`, `zen_tombstones`: response history and permanent deleted-object identity |
| Legacy links | `link_groups`, `link_bank_members`, `link_zen_members`: confirmed 1:N/N:M, including both inter-account transfer sides |
| Exclusions/categories | `bank_exclusions`, `zen_exclusions`, `recipient_rules`, `category_choices`, `category_evidence` |
| Migration archive | `legacy_imports`, `legacy_objects`, `legacy_rows`: source schema, every row, and verifiable fingerprint |

Bank amounts are positive decimal **strings**, with direction stored separately.
Maximum 24 integer and 12 fractional digits; zero bank amounts are prohibited.
`save_record` additionally checks currency/scale and calendar date in the bank time
zone. Repeated records retain UUIDs; changed data creates a version without resetting
existing processing state.

ZenMoney amounts are text; complete JSON never passes through float. All entity
kinds, unknown fields, and original responses are preserved. Journal references to
ZenMoney UUIDs intentionally have no FK to the current mirror: removing a mirror
object must not erase proof of prior processing.

The database enforces unique bank identities/aliases and delivery UUIDs, alias account
ownership, FKs, immutable bank identity, and immutable started payloads. Versions,
attempts, and confirmations are append-only. `delivered` cannot reenter the queue;
`suppressed` cannot be restored. `INSERT OR REPLACE` does not bypass protections on
storage connections with `foreign_keys` and `recursive_triggers` enabled. Open every
financial mutation connection through `storage.transaction`/`configured`, not raw sqlite3.

B3 job handlers and B4 mirror/prediction are implemented. SDK network reads occur
outside transactions; raw diff, deletions, cursor, and predictions are applied
atomically. Imported legacy cursors do not prove freshness: a full B4 read is required.
B5 sends only authorized complete versions with viewed=false; crashes/unknown outcomes
do not authorize repeat writes. Schema changes alone create no authorizations.

## Explicit legacy v12 conversion

B2 did not migrate the production database. The command below is for a **separate,
agreed copy** during M1. `migration-input` must contain a SQLite copy including all
required WAL data; `migration-output` must exist and be writable by the specified UID.
Do not mount the old application's production volume into this command.

```sh
docker build --target runtime -t zenimport-backend:local .
docker run --rm --network none --read-only \
  --user "$(id -u):$(id -g)" --tmpfs /tmp \
  --mount type=bind,src="$PWD/migration-input",dst=/input,readonly \
  --mount type=bind,src="$PWD/migration-output",dst=/output \
  zenimport-backend:local python -m backend.migrate_legacy \
  --source /input/legacy.sqlite3 --destination /output/converted.sqlite3
```

The source opens with `mode=ro` in one read transaction, including committed WAL.
For a read-only mount, use a consistent copy without an active writer; backup tools/
real copy preparation belong to M1. The converter checks version/FKs/integrity,
converts inside a private temporary directory, validates the archive, then atomically
publishes a new mode-600 file. Existing files are never overwritten, even in a race.
Allow enough space for the new database, full row/schema archive, indexes, WAL,
and temporary sorting.

Every SQL object/row is archived, including unknown tables, obsolete drafts,
original payloads, and raw data. SQLite types are preserved: NULL, integer as string,
REAL as hex, unchanged TEXT, BLOB as base64. Archived SQL is **never executed**.
Normalization uses known v12 tables only. Unknown currencies, broken relationships,
conflicting UUIDs, or corrupt projections stop conversion without publication.

Repeating the same logical snapshot validates the archive content fingerprint and
returns `repeated=true` without changing financial data. Another source cannot be
imported into an existing destination. CLI output contains only counts/states;
errors never print transaction rows/tokens/payloads.

## Migration policy

- Preserve account/bank transaction/group/ZenMoney UUIDs, exact amounts, raw data, exclusions, rules, current mirror, original responses, and cursor.
- Preserve old mappings in `account_bindings`. Accounts remain `unconfigured`: B3/E2 must confirm access profile, period start, and user selection. Legacy imports do not prove interval completeness, so never invent `coverage` or `last_complete_end`.
- Confirmed groups and prior `linked/sent` retain processing even without a current mirror record. Audit `creation_verified`, `sent`, and manual `confirm/link` confirmations count even if the old UI removed a link. Explicit soft/hard deletions suppress retries; archives never restore the mirror.
- Started or not-provably-unstarted requests retain UUIDs and the **exact text** of current payloads. `conflict/uncertain` become `uncertain`; an expected UUID in the mirror proves existence even after user field changes. Phone data is never corrected.
- `prepared` alone is insufficient: inspect `creation_local`, `creation_intent`, and `creation_retry_prepared`. Previous UUID attempts for the same payment also block blind delivery of a new UUID. Extracted attempts preserve numbers losslessly; exact original audit text remains archived.
- Only proven unstarted requests are rebuilt through the SDK with their original UUID, sign-based direction, eligible manual category, and `viewed=false`. If target information is insufficient, retain the old payload with `needs_rebuild=1`; B5 must block delivery until safe preparation.
- Legacy edits to existing ZenMoney records and debt/transfer types remain archived. Eligible manual categories for new payments migrate separately. Automatic predictions do not become confirmed examples; B4 rebuilds `category_evidence`/`category_payments` from retained links and the fresh mirror only after the user reviews the corresponding payments.

## Validation

`scripts/test-docker.sh`: synthetic v12 with split payments, settlements, inter-account
transfers, deletions, unknown attempts, old drafts, unknown fields/BLOBs; exact VND/USD;
repetition; corruption/race/failure; WAL; B1→B2 rollback and backup. Then CLI and health
against a converted database, HTTP/auth, and preservation of financial data after
container recreation. Neither production database nor real API is used. A real-copy
rehearsal and mobile acceptance remain M1/M3.
