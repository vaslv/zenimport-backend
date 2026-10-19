# Bank and ZenMoney discrepancies

On October 16, 2026, the user restored one separate extension page: a read-only view of potentially extra records for the current bank. Corrections, deletions, and review happen in mobile ZenMoney. Automatic categorization and delivery are unchanged.

## Contract

Authenticated GET `/api/v1/connections/{uuid}/discrepancies` accepts optional `from_date`, `to_date` (YYYY-MM-DD), `account_id`, and `refresh` (bool). By default, the period starts at the earliest enabled account import_start and ends on the current bank date; maximum 3661 days. account_id must belong to an enabled account in this connection. The response schema is `DiscrepancyReport` in backend/discrepancies.py, exposed in OpenAPI. This addition is compatible with API v1 and requires no SQLite migration.

Response: bank/connection_id, period bounds, mirror refreshed_at, accounts with currency/both account names/checked_days/unchecked_days/status; days (date + bank account, exact string bank_total/zenmoney_total/difference, ZenMoney transaction list with id/amount/payee/comment/categories/created). Amounts count only movement on the target account, including transfers and debts; the other currency transfer side is not added.

## Building the list

1. Only discovered, enabled, supported accounts in this connection with an explicitly selected ZenMoney target. A target linked to several enabled bank accounts produces a warning instead of an ambiguous report.
2. Only days after import_start within confirmed bank coverage; days lacking complete history are not declared reconciled. Capture and delivery job states do not block comparison of already completed coverage. Pending, failed or uncertain delivery does not hide records.
3. Immutable bank record versions from completed intervals are used; partial new observations never replace proven history.
4. Persisted receipts/deliveries/groups only break ties between current equal movements on the same account and date. A known UUID with a changed amount, date or account is still compared. Deleted records and holds are excluded from the ZenMoney side; unmatched bank records remain visible.
5. Match exact signed amounts, then a unique combination of same-direction parts on the ZenMoney side. A balanced daily total does not prove a match. Search is limited to 16 parts and 20,000 steps per day. Equal amounts are matched stably by created/id; ambiguous remainders remain candidates. None of these links is written to the database.
6. Remaining bank records are returned as `bank_operations`; remaining ZenMoney records retain the `operations` field. Both lists use id/amount/payee/comment/categories/created; bank descriptions are in comment, with empty categories and null created. Both lists are shown separately. Extra expenses/income that cancel each other remain visible when the bank has no unmatched remainder. Days without candidates are omitted.

These are **review candidates**, not proof that a record should be deleted: ambiguous history cannot always identify a unique extra record. Compensating differences remain visible even when the day balance is zero. The report creates no links, jobs, or delivery authorizations and calls neither upsert nor delete. Ordinary mirror refresh can confirm a previously sent UUID through the existing journal; this is not a resend. The report reads the mirror; refresh=true forces a read-only ZenMoney refresh. A stale mirror is never presented as current after an error. Bank history is updated using the existing Synchronize button.

Validation: tests/test_discrepancies.py and extension integration check bank/day scope, coverage, changed known UUIDs, delivery independence, both unmatched sides, splits, transfers/debts, extra compensating records, mobile deletion, freshness, authorization, and absence of ZenMoney API writes when opening the report.
