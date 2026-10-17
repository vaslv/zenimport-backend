# Account settings and history capture — B3

Implemented connection registration, target catalog, account settings, jobs, leases,
pages, complete, capture-status, and stop routes from [sync-api-v1.md](sync-api-v1.md).
The v1 HTTP contract is unchanged. SQLite schema **5**: own v1/v2/v3/v4 databases
upgrade transactionally with a private backup; financial data and UUIDs are preserved.

## Discovery and settings

The canonical key consists of bank, identity scope, profile for scope=profile,
source key, and currency. scope=bank reuses UUID/settings/coverage across connections;
identical titles do not merge accounts. Support and presence are stored separately
for each connection/account pair. Partial lists preserve previous accounts. Complete
lists mark missing accounts and stop their unfinished capture while retaining history
and settings.

The server currently knows VietinBank: Asia/Ho_Chi_Minh, scales VND=0, USD/EUR=2,
and the legacy parser's details allowlist. These are normalized-data rules,
**not confirmation** of all currencies/accounts/history depth in the actual bank.
The connector reports actually supported accounts; live verification belongs to E3/M3.
VietinBank rejects details_state=unavailable: details are available and must be captured.
A legacy placeholder connection without capabilities requires actual rediscovery
before capture.

GET /zenmoney/accounts reads the mirror. service_meta `zenmoney_refreshed_at` must be
Unix seconds no older than 300 seconds. Returns regular nonarchived/nondeleted accounts
with a known instrument currency; debt/loan excluded. Missing freshness returns 503
zenmoney_unavailable. B4 refreshes the mirror through the SDK outside the transaction
before catalog reads. Freshness is saved atomically with cursor/data; see
[mirror-predictions.md](mirror-predictions.md).

Enabled requires a same-currency target and valid start date. Settings versions are
checked atomically: changes increment the version; an exact repeat with the previous/
current version is safe; another stale value returns 409. Start date is explicit;
30 days / six calendar months / custom date selection belongs to popup E2/E4.

Settings changes cancel only this account's unfinished intervals. Old job settings/
targets remain snapshots. An unstarted prepared payload retains its UUID, gets
needs_rebuild=1, and loses its prior authorization. An uncertain/started delivery
prohibits target changes, including through intermediate disabling. Disabling an
account is always permitted. Delivered records are never rebound.

## Periods, commands, and capture leases

- Initial capture starts at import_start. Incomplete history fills gaps first and skips proven complete islands.
- last_complete_end ends continuous coverage from import_start; it is not the latest transaction date or an independently completed interval's end.
- Ordinary continuation starts at max(import_start, frontier−14 days). The end is frozen to the bank date at job creation. After a long pause, the original snapshot resumes with its original end.
- Intervals are inclusive, merged, and split by max_window_days. Maximum 10,000 intervals per job.
- reimport_from bypasses old coverage. Remaining work is stored in reimport_ranges: the next ordinary click resumes it even with prior full coverage. Another settings version does not inherit this authorization.

job_requests stores the command UUID and canonical body. Repeats return the original
job; a different body with the same UUID returns idempotency_conflict. A new command
joins an active job; another reimport_from returns account_busy. Job account scope is
frozen; newly enabled accounts enter the next job. Disabling is immediate. Transactions,
an active-job unique index, and account_capture_locks protect shared accounts from
concurrent capture.

Leases last 120 seconds. The same worker renews them; takeover after expiry increments
epoch. Old epochs cannot submit new data. Expiry preserves pages and pauses capture.
Paused jobs retain account locks until resume/stop. Commands, leases/epochs, pages,
and snapshots survive restart.

## Ingestion and completeness

A page and all its transactions/versions are saved in one transaction before ACK.
Checks cover page number, totals, period/time zone, currency/scale/balance, details,
interval/job/account ownership, settings, and lease. Identical retries return ACK
without a new lease even after completion/stop/disabling; records/counters are unchanged.
Worker/epoch are excluded from the fingerprint. Changed content for the same page
number or changed totals fail the unfinished snapshot while preserving the original page.

Complete requires pages 1..N, consistent totals, exact count, and unique bank identities
across the entire interval. captured_records references immutable bank_versions.
Cross-page duplicates roll back the entire rejected page, including new records/versions.
Missing pages can be submitted later; until then, history_incomplete with no coverage.
An empty result is page 1/0 with [] followed by complete; it also advances the frontier.

Bank login required retains a paused snapshot until the next click. Connector reports
of changed context/broken completeness fail the snapshot even if the client requested
paused; the new job gets new interval IDs. Connector version/capability changes also
terminate old snapshots. One account's failure neither removes other data nor prevents
completion of remaining intervals. Stop cancels only unfinished capture.

The backend cannot prove actual bank completeness when browser totals/identities are
false. Correct traversal, details, and account-change detection belong to E3/M3.
B3 validates protocol consistency and never substitutes a client complete flag for
server verification.

## B4/B5 handoff

job_accounts retains settings versions, target, start/authorized dates; job_context
retains connector version/time zone/capabilities. captured_records links a bank version
to an accepted page. Coverage appears only after complete. Confirmed pages, commands,
and snapshots are immutable.

B5 selects versions from completed intervals/verified legacy data, rechecks current
enabled/version/target before every attempt, and respects needs_rebuild and the
permanent B2 journal. A newer version in an incomplete interval does not authorize
sending it. Stop/Chrome closure do not revoke delivery of already authorized complete
intervals; disabling prevents new attempts. B5 persists authorization for a specific
complete version at start/complete and wakes an independent worker. Capture and
delivery progress are separate; unknown outcomes are not delivered. B4 saves preliminary
predictions with page ingestion; these do not authorize incomplete-interval delivery.
See [delivery.md](delivery.md).
