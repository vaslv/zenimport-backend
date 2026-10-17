# Potential extra ZenMoney transactions

On October 16, 2026, the user restored one separate extension page: a read-only view of potentially extra records for the current bank. Corrections, deletions, and review happen in mobile ZenMoney. Automatic categorization and delivery are unchanged.

## Contract

Authenticated GET `/api/v1/connections/{uuid}/discrepancies` accepts optional `from_date`, `to_date` (YYYY-MM-DD), `account_id`, and `refresh` (bool). By default, the period starts at the earliest enabled account import_start and ends on the current bank date; maximum 3661 days. account_id must belong to an enabled account in this connection. The response schema is `DiscrepancyReport` in backend/discrepancies.py, exposed in OpenAPI. This addition is compatible with API v1 and requires no SQLite migration.

Response: bank/connection_id, period bounds, mirror refreshed_at, accounts with currency/both account names/checked_days/unchecked_days/status; days (date + bank account, exact string bank_total/zenmoney_total/difference, ZenMoney transaction list with id/amount/payee/comment/categories/created). Amounts count only movement on the target account, including transfers and debts; the other currency transfer side is not added.

## Building the list

1. Only discovered, enabled, supported accounts in this connection with an explicitly selected ZenMoney target. A target linked to several enabled bank accounts produces a warning instead of an ambiguous report.
2. Only days after import_start within confirmed bank coverage; days lacking complete history are not declared reconciled. Reporting for an account pauses during capture or when the period has pending/uncertain delivery.
3. Immutable bank record versions from completed intervals are used; partial new observations never replace proven history.
4. Persisted receipts/deliveries/groups exclude known ZenMoney transactions. User changes to amount, type, date, or category on a known UUID do not make it extra. Deletions and holds are excluded from candidates.
5. For remaining records, first consider the balance of the entire unmatched daily group (possible split/settlement), then exact amounts, then a unique combination of parts on one side. Search is limited to 16 parts and 20,000 steps per day. Equal amounts are matched stably by created/id; ambiguous remainders remain candidates. None of these links is written to the database.
6. Only remaining ZenMoney records are shown. Extra expenses/income that cancel each other remain visible when the bank has no unmatched remainder. Days without candidates are omitted.

These are **review candidates**, not proof that a record should be deleted: ambiguous history cannot always identify a unique extra record. A balanced unmatched group can conceal compensating errors; a conservative result was chosen. The report creates no links, jobs, or delivery authorizations and calls neither upsert nor delete. Ordinary mirror refresh can confirm a previously sent UUID through the existing journal; this is not a resend. The report reads the mirror; refresh=true forces a read-only ZenMoney refresh. A stale mirror is never presented as current after an error. Bank history is updated using the existing Synchronize button.

Validation: tests/test_discrepancies.py and extension integration check bank/day scope, coverage, known UUIDs, splits, transfers/debts, extra compensating records, mobile deletion, freshness, authorization, and absence of ZenMoney API writes when opening the report.
