# Reliable delivery of new transactions — B5

`backend/delivery.py`, SQLite **6**. Uses pinned SDK 0.6.1:
`transactions.expense/income`, `Client.upsert`, `Client.read_diff`.
This sends new records. Existing transaction editing stays in mobile ZenMoney;
the backend never restores user deletions. The v1 HTTP contract, popup, and
connectors were unchanged in this stage.

## Delivery authorization

A new POST `/api/v1/jobs` command authorizes delivery of eligible accumulated history
for selected enabled accounts. Completing an interval adds its records to the same
queue. `delivery_authorizations` persists the bank UUID, specific immutable version,
complete interval, job, settings version, ZenMoney target, and SDK source.
Repeating request_id never creates another job or UUIDs.

Select the newest **provably complete** version in the authorized period. A newer
version from an incomplete interval never replaces it. Triggers/FKs validate the
authorization basis; after the first attempt it cannot change or be removed.
An old row, prepared payload, or legacy import alone does not prove bank completeness.
Without a complete interval, reread it through ordinary synchronization before sending.
Conversion/upgrades never invent authorizations.

Excluded/already processed transactions are skipped. Permanent confirmations, groups,
attempts, and deletions survive independently of the current bank version. Before
first delivery, match against the fresh mirror: bank reference first, then target
account/day/direction/instrument/exact amount. Repeated amounts match by time and
multiplicity. The October 16 clarification below supersedes unconditional creation.

## One executor

The worker belongs to server lifespan and starts automatically. It is independent
of popup, bank tab, capture lease, and Chrome availability. Restart resumes only
previously authorized work; timers create no new jobs. Polls every five seconds;
start/complete wake it earlier.

`<actual database path>.delivery.lock`, mode 600 with OS flock, excludes a second
sender, including another process. It may remain after stopping; ownership depends
on the OS lock, not file existence. Never delete it while running. This protects one
local-disk service, not a cluster/shared network SQLite volume. One process remains
recommended. SDK exchanges/cursor updates also serialize with catalog reads.

Before **every batch**, force mirror refresh outside a SQLite transaction. A short
transaction then rechecks enabled, settings version, target, currency/regular active
account/instrument/owner, completeness, and permanent history. A token change invalidates
old authorization and needs another explicit click. One unavailable target does not
block eligible delivery to other accounts.

Disabling before intent commit prevents the request. After intent, the request is
considered started and only completed/verified; no next batch is sent. Target changes
need new authorization; unstarted prepared retains its UUID. Stop halts capture but
does not revoke already authorized complete intervals. Disable the account to pause
new delivery attempts.

## Payload and attempts

Batches contain at most 20 transactions. This is an implementation batch size, not
an API limit claim. Direction comes from the bank, exact amount is Decimal, date is
the bank-local date, payee is the recipient name, comment is the description.
`created` is Unix `occurred_at` from the authorized immutable bank version, accounting
for time zone; `changed` is send time. This preserves within-day chronology. Legacy
versions without bank time use send time rather than inventing transaction time.
`viewed=false` is **explicit**, even for confident categories.

B4 computes an eligible category for the authorized version; unknown is NULL.
ZenMoney may assign a category in the first creation response: stored original
payload versus response mirror distinguish our prediction from remote enrichment.
Never overwrite that result. No automatic debts/transfers/Adjustment.

Before the first network byte, one transaction persists UUID, complete exact payload,
attempting/ever_attempted=1, bank journal uncertain, and append-only intent event.
Prepared without prior attempts can safely rebuild using the same UUID. After any
attempt, payload, UUID, and authorization basis are immutable. Network calls hold no
SQLite write lock. Results are appended as separate events, never edits to old ones.

Returned diff applies atomically through B4: raw, projections, deletions, cursor,
predictions. Confirm **by UUID**, never by comparing sent fields: the user may already
have edited category, amount, or type on the phone. For unconfirmed UUIDs, perform
another read-only fetch with `forceFetch=['transaction']`; never another upsert.

## Unknown outcomes and deletion

Timeout, HTTP 400/401/429/5xx, incomplete/malformed responses, and crashes do not prove
that the API applied nothing. A crash in attempting becomes uncertain; only reads
of existing UUIDs are permitted. UUID presence confirms delivery; explicit soft/hard
deletion makes the bank transaction terminal suppressed. Never correct or restore
mobile edits/deletions. UUID absence, even in a full response, never authorizes retry.

This is deliberate: a crash **after intent persistence but before the actual request**
can also leave uncertain. Without proof of nonapplication it will not be resent
automatically. The SDK cannot report whether the server applied a request or reliably
prove nothing was written from an HTTP code alone. B5 has no editor/force-retry button;
never create another identity or reset journals to bypass the block. Later fetches
can confirm the old UUID; elapsed time alone cannot authorize a write.

Retries apply only to **reads**: B4 has one bounded transport/429/5xx retry; across
cycles, delays persist as 15/30/60/120/240/300 seconds. Restart does not reset them;
a new user click requests another verification. SDK HTTPError exposes no Retry-After.
An unknown batch remainder blocks overall success without repeating or losing the
already confirmed portion.

## Status and shutdown

JobView retains pending/delivered/suppressed/uncertain counts and separate
capture_state/delivery_state. A captured UUID never disappears from job progress
because its bank date changed later. `running` means a started attempt; `blocked`
means unknown outcome, unavailable target/SDK, or changed settings; `complete` means
complete capture and all its records processed/suppressed. Partial delivery success
never implies complete overall capture. Error `retryable` permits command/read
retries, not ZenMoney writes.

During graceful shutdown, the worker stops new attempts, waits for the current
exchange, and closes the SDK. Docker retains the previous 30-second grace; if killed
before a request finishes, intent journaling supplies the same safe recovery path.
Health never claims financial success, and errors/logs contain no tokens, source
rows, or API responses.

## Validation and next stage

`scripts/test-docker.sh`: SDK MockTransport in a network-isolated container; exact
expense/income with viewed=false and category/NULL; 20+20+2 batching; learning
threshold; complete/incomplete versions and legacy backlog; exclusions/confirmations;
lost response; partial application; HTTP rejection before application; mobile edits/
deletion; multiple accounts; target/token change; disabling; concurrent workers;
backoff; intent rollback; lifespan; stopped browser requests. Separate container
processes use os._exit before requests and after fake API commit; recovery checks
the same UUID without upsert. Synthetic v4→v5 upgrade/rollback with private backup.
Total **340 passed**, 48 new B5 checks, Docker 29.4.0 / Compose 5.1.2,
linux/arm64 / CPython 3.12.15. Runtime mode=rw never recreates a database removed during
operation; the background-worker race was checked. HTTP/persistent-volume checks
passed after recreation, including delivery authorization preservation.

Full actual results/commit: [log](refactor/05-progress.md). Real API/database/mobile UI
were not tested. Later B6 passed ARM64/emulated AMD64: [b6-validation.md](b6-validation.md).
viewed=false is verified in payloads; mobile New display remains M3. Next: E1,
Chrome extraction; independent B6 is complete, while E5 requires the new extension.

## Timestamps and categories: October 16, 2026 validation

Fixed created previously taking synchronization time for every batch transaction.
New records use bank time; changed remains current. Time comes from the same
authorized version as date/amount, even if the current bank record has changed.
Existing sent payloads/UUIDs are never rewritten.

API docs distinguish date (calendar date), created, and changed:
https://github.com/zenmoney/ZenPlugins/wiki/ZenMoney-API#transaction.
ZenMoney support says created stores bank time:
https://support.zenmoney.ru/communities/1/topics/2465-strannoe-povedenie-changed;
existing-record created updates are unsupported:
https://support.zenmoney.ru/communities/1/topics/2316-obnovlenie-tranzaktsij-cherez-api.
Actual mobile rendering after this change needs live acceptance, not fake API evidence.

Read-only production diagnostics found different bank times for two checked records
but identical original payload created timestamps. Both categories were NULL when
sent and already filled in the first ZenMoney delivery response. Local confirmed
history/rules/manual categories were empty. This is remote categorization, not a
wrong backend prediction. Personal payloads/payees/secrets are absent from these
docs. No automatic disabling/clearing of remote categories was implemented: public
docs establish no such mechanism, and actual records were not used for experiments.

## Recognizing prior history: October 16, 2026

Discovered duplicates came from starting a new database without prior confirmed
links. Mirror presence alone previously did not prevent creation: each new bank_id
got a new ZenMoney UUID. This was not an HTTP retry: production history has exactly
one intent and one response_applied for each of 518 UUIDs.

Before first intent, existing records are now checked for unambiguous identity:
VietinBank bank reference `vib:id:…` as a separate comment token, target account,
calendar date, direction, exact amount, and bank-side instrument. Transfers/debts
use the bank side specifically. A unique record unoccupied by another confirmation/
attempt/group/exclusion becomes a permanent origin=bank_reference processing_receipt;
no upsert. Competing bank records cannot claim one UUID. Unstarted prepared is
removed; started/uncertain payloads/UUIDs are never reassigned. Mobile deletion of
a recognized record remains final.

A second user-requested level uses target account, calendar day, direction, instrument,
exact amount, then chronological ordering among equal amounts. Match the minimum of
bank count and free ZenMoney sides; create only the positive difference. One UUID
bank side cannot consume multiple bank records. One transfer may match expense on
one account and income on another. Another reference in a description does not
forbid day/amount matching; references establish search priority only. When two
physical bank accounts share a target, amount matching is disabled rather than
guessing ownership.

Receipts use origin=day_amount for unique pairs and day_amount_order for repetitions.
The latter never trains categories: chronology preserves multiplicity but cannot
prove individual payee identity. Matched UUIDs and later mobile edits/deletions retain
the existing finality rules. This is heuristic: unrelated manual records with equal
account/day/amount cannot always be distinguished without a stable reference.
Mobile date/amount edits before first observation may also conceal a match.
Never clear the permanent journal during ordinary updates.

For the cleanup, the stopped old volume was read read-only without restarting its
service or migrating the whole database. Private Git-ignored
`data/duplicate-cleanup-plan.json` records exact new-copy/retained-original UUIDs from
prior confirmed single links, validated against current date/account/amount.
Changed/reviewed copies are separate. The user explicitly authorized deletion of
these 493 new copies. Manual backend.duplicate_cleanup is unavailable to HTTP/sync
jobs. It validates the approved plan hash, fresh snapshots of both sides, copy
ownership by our delivery journal, and absence of other links. Before I/O, it saves
a private backup, canonical single links to retained originals, and durable intent
history. Batches contain at most 20, each followed by reads/verification. Reusing an
existing journal verifies only; it never repeats financial requests. Retired-copy
deletion never suppresses the retained original; later deletion of the original is
final. Financial rows/UUIDs/API responses never enter Git. This audit does not turn
an amount heuristic into a proven bank link.

## All types and composite matches — October 16, 2026

Search uses signed movement on the target account: income minus outcome on its sides,
with matching instrument/exact Decimal amount. Expenses, income, refunds, debts, and
transfers participate equally; the transfer's other-account currency/amount never
replaces the bank-side amount.

After single matches, search same-day/account unique groups: one bank record to
multiple ZenMoney records (split); multiple bank records to one ZenMoney record
(combined fee/settlement); one bank record to multiple opposite-direction movements.
Sum signed amounts. Persist origin=day_amount_group link_groups and all participants.
Group creation and withdrawal of unstarted delivery commit together before first
intent. Rebuild training history before predicting new records after recognition.
A split counts as one independent payment; settlements never train categories.

Combination search is limited to 16 participants on either side and 20,000 visits
per account/day. Never invent a match for multiple alternatives, overlapping proposals,
or exhausted search; exhaustion cancels all that day's proposals. Zero settlements
without a ZenMoney record are not automatically suppressed. Remaining records follow
the agreed new-unreviewed-record creation policy. Irreducible ambiguity therefore
makes elimination of every possible duplicate mathematically impossible.

Started deliveries/known UUIDs are never reassigned. This prevents future creation
when matches exist; it neither deletes current duplicates nor corrects mobile edits.
Later explicit deletion of a recognized group member remains final and never
authorizes recreation of its bank records.

## Cleanup of agreed composite duplicates

Manual duplicate_cleanup supports the previous single-pair plan and a separate groups
format: kind, bank_ids, keeps (complete snapshots), removals (bank_id and complete copy
snapshot). The SHA256-approved plan validates independent identifiers, one account/day,
and exact signed totals, including the currency transfer bank side. Each bank record
has exactly one confirmed delivery selected for deletion. Any checked original/copy
mobile edit stops deletion.

Before deletion, save a private SQLite copy, plan, and fsync journal, then a canonical
single/split/settlement group. Mirror logic considers the complete retained UUID set:
deleting retired copies never suppresses the original bank transaction; deleting a
canonical member remains final. Deletion confirmation requires a tombstone, not mere
mirror absence. A journaled rerun reads/verifies only, sending neither repeat nor
subsequent financial requests.

Fake-API automated checks only: 442 tests in the full Docker run, including both
composite directions, settlements, currency transfers, changed snapshots, wrong
date/account/amount, overlap, incomplete plans, and response loss before/after
application. Actual cleanup is a separately explicitly authorized action.

## Matching side after remapping — review fix

`processing_targets` records the specific ZenMoney account for each bank_id/zen_id
pair. Immutable rows commit with acceptance of an existing transaction/group or a
send intent. `account_bindings` history no longer occupies every side of every transfer.

v5→v6 migration creates a private backup and recovers only proven targets from
immutable sent payloads and retained matching authorizations. Current mappings/mobile
edited records are not evidence. For old duplicate_cleanup groups, original-copy
authorization does not prove the retained original's account.

Without evidence, preserve the old link without inventing an account. New unmatched
records for affected accounts/days remain pending, with existing_ownership_unknown.
Other accounts/days continue. This restriction concerns unknown ownership history;
ordinary ambiguous day/amount matches retain prior policy. Restoring a proven target
clears the block on the next cycle; no manual history deletion/UUID reset is needed.
