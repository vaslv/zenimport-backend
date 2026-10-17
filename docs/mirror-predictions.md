# ZenMoney mirror and category prediction — B4

Implementation: `backend/mirror.py`, `backend/predictions.py`, introduced with SQLite
schema **4**; current storage version: [financial-storage.md](financial-storage.md).
The vendored SDK 0.6.0 is used directly. This stage performs only
`Client.read_diff` reads, with no ZenMoney writes/updates. Sender, background delivery,
and target revalidation immediately before writes are implemented in B5:
[delivery.md](delivery.md). This document describes the read-only B4 module.
The v1 wire contract and extension interface are unchanged.

## Mirror refresh triggers

- GET `/api/v1/zenmoney/accounts` and enabled-setting updates obtain a fresh catalog; successful snapshots can be reused for 300 seconds.
- New POST `/api/v1/jobs` commands force mirror refresh. Exact request_id repeats return the original job without another fetch.
- Bank page ingestion never calls the SDK: transactions/preliminary predictions are saved with the page before ACK.
- `/api/v1/info` only reads state, never the network. `ready` means a successful mirror no older than 300 seconds; `unavailable` means stale/failed; `not_configured` means no token. Last successful read time survives failures and does not mean the mirror is currently fresh.

Starting the mirror alone makes no API calls. In B5, a worker may resume previously
authorized delivery/verification after server startup. Tokens never enter the extension,
error log, or database. Mirror state stores a token SHA256 to bind the cursor to its
source. Changing the token/disabling the SDK resets freshness and requires a new full
read while preserving the permanent financial journal. This is not a way to combine
different ZenMoney users.

## Atomicity and precision

Initial reads use cursor=0 with SDK `FORCE_FETCH`; subsequent reads use the incremental
cursor. The first snapshot requires explicitly present account, instrument, tag, and
transaction collections, including empty ones. Without them, freshness is not declared.
This conservative condition is tested with a fake API; the actual response of a new/
empty account must be checked in M3 before LIVE. Other collections are projected when
present; unknown collections remain in raw diff. An omitted collection never clears
its projection.

Network calls run outside SQLite transactions. One subsequent transaction persists
original JSON, every SDK entity kind, exact movements/categories, deletions, permanent
tombstones, cursor, generation, freshness, evidence, and predictions. Invalid responses/
projection failures roll back the entire application. The last successful snapshot/
cursor remain, but freshness is revoked. Monetary values use SDK Decimal/encode_json,
never float; unknown fields are retained.

Reads serialize within one process. Generation/source CAS additionally prevents stale
responses from overwriting newer snapshots/tokens. Runtime still supports one instance,
not a writer cluster. Transient reads (transport/429/5xx) retry once after 0.1 seconds;
400/401/403 and invalid responses do not retry. SDK HTTPError does not expose
Retry-After; this mechanism does not authorize write retries.

SDK failure never rolls back an existing job/bank pages: capture can continue while
delivery_state becomes blocked with zenmoney_unavailable. Successful refresh clears
only that blocking reason; it neither authorizes uncertain deliveries nor replaces
B5 target account validation.

## Respecting mobile data

Confirmation uses only known UUIDs from deliveries, attempts, permanent receipts,
and historical groups. A found UUID proves existence regardless of mobile field
changes; payloads are never corrected. Explicit soft/hard deletion permanently
suppresses the corresponding bank transaction. Even later reappearance of that UUID
does not requeue it. Absence from a full or partial response proves neither deletion
nor failed prior creation: uncertain remains uncertain.

The mirror itself does not match semantic similarities. Before new delivery, B5
persists the user-requested account/day/direction/instrument/exact-amount match.
An origin=day_amount_order receipt never trains categories: timestamp ordering of
equal amounts does not prove the payee. Agreed manual duplicate cleanup creates
canonical single groups for originals; retired copy UUIDs stay journaled, and their
deletion never suppresses the original.

## Prediction

Priority: eligible manual category_choices → unambiguous explicit recipient_rules →
at least **three independent confirmed payments** in one category without conflicts →
NULL. Unknown transactions are not assigned Adjustment. Candidates sort by independent
payment count, then title and ID. Prediction requires a fresh mirror and a category
valid for the expense/income direction. Manual choice is separate from the projection.

History is partitioned by canonical bank account, direction, and payee: Beneficiary
account → normalized name when account is absent → exact normalized description when
both are absent (at least six characters). Normalization uses only Unicode NFKC,
case, and whitespace; bank references are not stripped and fuzzy similarity is not
used. Known accounts are not replaced by descriptions for matching. Explicit name
rules apply only within the specified bank/direction; ambiguous rules never yield
an automatic category.

Eligible examples are explicitly linked delivered payments on a known regular ZenMoney
account with matching currency, direction, and exact amount. Every part must be
viewed=true, with no hold/deletion/exclusion. Our own unreviewed records never train
the model, even when ZenMoney assigns a category in its first tag=NULL response.
That response is remote state, not confirmation of our prediction. A single-category
split counts as one example; a mixed/uncategorized reviewed payment blocks confidence.
Settlements/N:M, transfers/debts, and one UUID shared by several bank payments yield
no examples. Repeated fetches do not increase counts. Evidence is rebuilt from current
data: category edits change statistics, deletion/type changes remove prior evidence.
A payment never trains its own prediction.

## B4 → B5 handoff

- Call `Mirror.refresh(force=True)` before sending, **outside** an open transaction; recheck settings/current target afterward.
- `category_payments`/`category_evidence` are recomputable evidence; `category_predictions` is a preliminary current-bank-version projection with source, reason, candidates, and mirror generation. It does not authorize incomplete-interval delivery.
- `predictions.predict(db, bank_id, now, revision=...)` selects the version from a completed interval rather than a newer incomplete one.
- Unstarted prepared delivery gets needs_rebuild=1 when version/category changes. B4 replaces neither UUID nor payload. Attempts, uncertain, delivered/suppressed are excluded from preparation; started payloads are immutable.
- B5 builds SDK expense/income with viewed=false and a category or NULL, respecting permanent confirmations/deletions and B3 full-coverage rules.

## Validation

`scripts/test-docker.sh`: **292 passed**, 57 new B4 checks, SDK MockTransport in Docker
without networking. Full/incremental reads of every kind, precision/raw, soft/hard
deletion, mobile edits, absent UUID, errors/rollback, read retries, generation/token
races, freshness/restart, capture during SDK failure, threshold 2/3/5, split/N:M/
exclusions, viewed=false, directions, payees/descriptions, manual rules, and immutable
started payloads. Synthetic v3→v4 private backup/rollback verified. HTTP/auth, job
resumption, and preservation after container recreation also passed. B4 used
ARM64/CPython 3.12.15; B6 later checked emulated AMD64:
[b6-validation.md](b6-validation.md). Real API/database/mobile UI were not tested.
Actual migration remains M1 and acceptance M3; the sender is implemented in B5.
