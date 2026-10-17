# Synchronization contract v1 — B0

October 15, 2026. Status: fixed for B1–E5 implementation. The independent backend
has completed B1–B5: health/info, settings/capture, mirror/prediction, and durable delivery.
Rules: `docs/delivery.md`. Models: `backend/sync_contracts.py`; examples:
`fixtures/sync-v1/`. Schemas: `json_schemas()`, JSON Schema 2020-12.

## Boundaries and project extraction

SDK `/Users/vaslv/Projects/Vaslv/ZenMoneyAPI` remains general-purpose. The backend
becomes independent `ZenImportBackend`, Chrome becomes `ZenImportChrome`. Connectors
are extension modules. The original repository remains as history. B1/E1 create local
repository copies without hardlinks/remotes, record the source commit, then remove
unneeded paths in separate commits. This retains history without rewriting the
original; previous interfaces remain accessible through Git. Final working trees
contain only their project's files. Renaming the source directory, pushing, tags,
and publication are not required.

| Responsibility | Owner |
|---|---|
| ZenMoney HTTP protocol / error types / exact amounts | SDK |
| History, mappings, coverage, predictions, queue, deletions | Backend |
| Popup, bank session, page/detail capture | Chrome |
| Account list, IANA zone, windows, pagination, parser | Bank module |

No financial editor, preview, reconciliation, or bank UI elements. Bank passwords/
cookies/OTPs never leave the browser session. The SDK token stays on the server.

## HTTP

All routes are under `/api/v1`, requiring the backend Bearer token. `/health` is
public and exposes no financial data/secrets. The API version is in the path;
`ServerInfo.api_version=1`. Required field/semantic changes need a new major API
version. New optional fields are coordinated between both builds: current models
reject unknown fields. `GET info` checks compatibility before reading the bank.

| Method and path | Request → response | Result |
|---|---|---|
| GET `/info` | — → ServerInfo | 200 |
| POST `/connections/discover` | DiscoverConnection → ConnectionView | 200 |
| GET `/connections/{id}` | — → ConnectionView | 200 |
| GET `/zenmoney/accounts` | — → AccountCatalog | 200; no ZenMoney writes |
| PUT `/accounts/{id}/settings` | AccountSettings → AccountView | 200 |
| POST `/jobs` | StartSync → JobView | 202; repeats return the existing job |
| GET `/jobs/{id}` | — → JobView | 200 |
| POST `/jobs/{id}/lease` | CaptureLeaseRequest → CaptureLease | 200 |
| PUT `/jobs/{job}/intervals/{interval}/pages/{number}` | CapturePage → PageReceipt | 200 after commit |
| POST `/jobs/{job}/intervals/{interval}/complete` | CaptureComplete → IntervalView | 200 |
| POST `/jobs/{id}/capture-status` | CaptureStatus → JobView | 200 |
| POST `/jobs/{id}/stop` | Empty JSON `{}` → JobView | 200; safe to repeat |

Maximum body 2,000,000 bytes and 500 transactions per page. Larger bank pages are
split by the connector into sequential transport pages with a fixed total count.
Normalized page numbering need not match bank UI numbering. Partitioning cannot
change within an interval. JSON dates are `YYYY-MM-DD`; times are RFC3339 with a
required offset. Interval dates are inclusive in the bank's IANA zone. The server
checks local_date against that zone's timestamp, period, account currency, and scale.

`amount` is a positive decimal string without exponent/sign/separators; direction
is separate expense/income. Up to 24 integer and 12 fractional digits, no more than
the account scale; the connector also normalizes excess trailing zeros. Balance uses
the same string format but permits negative/zero. Zero movement is not sent as income/
expense: the connector explicitly flags the unsupported source row and cannot claim
complete capture until its meaning is resolved. Never hide such rows in tests/docs.

## Context, accounts, settings

profile_key is a stable bank-provided identifier or a separate user-chosen connection
identifier, never inferred from the owner's name. identity_scope=bank requires proven
source_key uniqueness within the bank; otherwise use profile. Canonical key:
bank + scope + [profile_key for profile] + source_key + currency. Proven physical
account identity reuses existing UUID/settings; guessing from equal titles does not
merge accounts. Partial discovery does not close accounts.

Every account is unconfigured/enabled/ignored. enabled requires import_start and a
target: an active regular same-currency ZenMoney account. ignored/unconfigured has
no active target; prior mappings remain in delivery/audit history. expected_version=0
for unconfigured accounts; the server checks the current version atomically,
increments it on changes, and returns AccountView. Exact unchanged-setting repeats
are safe; stale differing values return 409. Future start dates, unsupported currency/
account, and invalid targets return 422/409. Revalidate the target before delivery.
Started/uncertain deliveries block target changes; delivered records are never rebound.
Disabling takes immediate precedence before the next request; in-flight requests
are only completed/verified.

New unconfigured accounts do not block already enabled connection accounts. Without
any enabled accounts, startup requires configuration or reports all accounts ignored.

## Jobs, completeness, idempotency

StartSync.request_id is the command UUID persisted by the browser before receiving
its result. The server stores it with the request body. Identical repeats return the
same job; different bodies return 409 idempotency_conflict. A new request_id joins
this connection's active job and records that result; reimport_from differing from
the active plan returns 409 account_busy rather than being lost. Another connection
capturing a shared account returns 409 account_busy. Transactions/database constraints,
not a single JavaScript flag, enforce this.

Resuming a known paused job requires a new command request_id. Repeating the old
request_id returns its state without resuming capture. If the original command response
was lost, retry the original UUID.

History ends on the bank's current date frozen at job creation. Per account: initial
period starts at import_start, then max(import_start,last_complete_end−14). Fill initial
import gaps first. reimport_from requests one-time rereading regardless of old coverage;
failures preserve remaining work. Plans persist settings versions, intervals, and
request source. Last complete capture and delivery outcome are different. A user click
also authorizes delivery of eligible old pending records for enabled accounts.

One collector per job: worker_id persists in extension local state; leases last
120 seconds and renew before expiry. Takeover after expiry increments epoch. Another
valid lease returns 409 capture_busy. Every new write checks owner/epoch/expiry,
settings, and interval ownership. Progress reads need no lease; the server queue is
independent of the browser lease. Closing the popup does not release it; capture
stop/completion does. Expiry pauses rather than loses pages. Identical accepted-page
repeats may receive ACK without a new lease, excluding worker/epoch service fields.

Page key: (interval_id,page_number). Path and body numbers must agree. Persist page,
fingerprint, and transactions atomically before ACK. Exact repeats do not increment
counts; changed content at the same number returns 409. Changed totals/context fail
the interval without marking it complete. A new interval snapshot gets a new ID;
confirmed transactions/delivery history survive. The server allowlists connector
details fields; arbitrary raw HTTP responses/secrets are prohibited.

complete verifies pages 1..total_pages, consistent totals, counts, periods/account/
currency/details, and absence of lost rows. Missing/inconsistent pages return 409
history_incomplete. An empty interval has one page, totals=1/0, and []. No client
trust-me-complete flag exists. details_state=unavailable is allowed only when the
connector proves details are unavailable. Local Pydantic validates shape; these
checks require the database/bank registry.

stop halts only capture. The backend continues authorized complete-interval delivery;
disabling separately pauses future writes too. Login required pauses capture; resume
after sign-in and the next click. Never independently start a new job on page load
or a timer.

## Delivery and learning

States: pending → prepared (UUID/payload persisted) → attempting → delivered or
uncertain. A crash during attempting means uncertain; known deletion means suppressed.
Delivered never becomes pending after mobile edits or absence from partial diff.
Uncertain verifies the prior UUID through diff without blind upsert, a new UUID, or
restoring old field values. An HTTP status alone cannot prove retry safety. The
server queue survives Chrome closure.

Expense/income, explicit viewed=false; eligible manual categories take precedence,
then confirmed rules/three independent consistent examples; otherwise category null.
Our own viewed=false predictions never train. Current ZenMoney category/type/deletion
changes affect learning evidence, not the original bank record.

## Errors and results

ErrorResponse = `{error:{code,message,retryable,request_id}}`. UUID request_id enables
anonymous diagnostics; message contains no request body/tokens/account details.
401 unauthorized; 404 not_found/unsupported_api_version; 405 method_not_allowed;
500 internal_error; 409 conflicts/leases/incompleteness; 413 payload_too_large;
422 validation_error/unsupported_currency; 502/503 ZenMoney unavailable. Unknown
delivery and expired bank login statuses also appear in JobView.errors. retryable
permits retrying reads/commands by key, **not** ZenMoney writes. No error exposes
Pydantic input over HTTP.

JobView separates capture_state/delivery_state, per-account progress, and four delivery
counts. suppressed means deliberately excluded/known deleted; uncertain is not delivered.
Never claim full success for cancelled/paused/failed or blocked/uncertain. The account
catalog returns refresh time and eligible targets only; failed retrieval never makes
an old catalog fresh.

## Discrepancy page (October 16, 2026)

Added authenticated read-only GET `/api/v1/connections/{uuid}/discrepancies`;
parameters/exact semantics: [discrepancies.md](discrepancies.md). This API performs no
corrections/deletions, link creation, or transaction delivery. The rest of v1 is unchanged.

### Unknown account in a previous match

`existing_ownership_unknown` in `JobView.errors` means a legacy ZenMoney transaction
link survives but evidence of its specific account is missing. The job has
delivery_state=blocked; affected new records remain pending without delivery while
other accounts/days continue. This is not an unknown outcome of a new request and
does not authorize retrying one.
