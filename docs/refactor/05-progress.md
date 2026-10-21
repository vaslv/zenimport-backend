# Implementation status

October 15, 2026: the user authorized sequential implementation and regular local commits.

| Stage | Status / result |
|---|---|
| B0 | Complete: docs/sync-api-v1.md, backend/sync_contracts.py, fixtures/sync-v1/examples.json; 56 Docker contract tests passed |
| S1 | Complete in ZenMoneyAPI, commit 880aa19; 244 tests, mypy, 99% coverage |
| S2 | Unnecessary: no SDK capability gaps found |
| S3 | Complete: existing wheel 0.6.0 installed in a clean independent backend container; checksum and exact unreviewed write verified |
| B1 | Complete: independent project, Docker runtime/config/tests, health/info, explicit settings/foreign database protection; 93 tests and HTTP/persistence smoke passed |
| B2 | Complete, ffecceb: financial schema v2, exact amounts/aliases/versions, permanent confirmations/attempts/deletions; explicit synthetic legacy v12 conversion; 169 tests and Docker smoke passed |
| B3 | Complete, 29315a5: account discovery/settings, mirror catalog, periods/jobs/lease, atomic ingestion/completeness; schema v3; 235 tests and Docker HTTP/resume smoke passed |
| B4 | Complete, 4cbd4f3: read-only SDK mirror/cursor/deletions and independent confirmed-payment predictions; schema v4; 292 tests and Docker HTTP/resume smoke passed |
| B5 | Complete, 825c9d4: complete-version queue/permanent UUIDs, SDK expense/income viewed=false, one worker, result verification/crash recovery without blind retries; schema v5; 340 tests and Docker HTTP/resume/persistence smoke passed |
| B6 | Independent portion complete October 16, c6a5c9f: 347 tests each on ARM64/emulated AMD64; HTTP/SIGKILL/graceful in-flight shutdown/snapshot restore passed; E5 integration awaits extension |
| E1–E5 | Not started at this checkpoint |
| M1–M4 | Not started; real data unchanged |

Next: E1, independent Chrome extraction. Independent B6 is ready; overall E5 integration follows E1–E4. Working directory: ZenImportBackend. B3 routes, read-only B4 refresh/prediction, and B5 sender are implemented; these checks never called the real API. Legacy server/extension continue separately. This is the backend status source; original ZenImport retains an extraction summary.

## Checks

- B6 (October 16): scripts/test-platforms.sh, Docker Engine 29.4.0 / Compose 5.1.2 / CPython 3.12.15: **347 passed on linux/arm64 and 347 on linux/amd64**, seven new snapshot checks. Actual Linux/aarch64 and Linux/x86_64 execution; AMD64 emulated through OrbStack on ARM64. Per platform: HTTP/auth/resume/persistence, SIGKILL after fake API commit, graceful in-flight shutdown (Application shutdown complete, exit 143), restart, consistent snapshot restoration to a new file; original UUIDs/identity, three viewed=false records, no repeat upsert. Verified UID 10001/read-only root, CLI export/restore, WAL, publication races/no replacement. Test resources removed; no real bank/API/database. Details: docs/b6-validation.md; operations: docs/operations.md. E5 not yet run.
- B5: scripts/test-docker.sh, Docker Engine 29.4.0 / Compose 5.1.2 / linux/arm64 / CPython 3.12.15: **340 passed**, 48 new checks. Stateful fake API through actual vendored SDK without network: exact expense/income/viewed=false, categories/NULL/learning, 20+20+2, completeness/versions/backlog, UUIDs/exclusions/old receipts, partial application, lost/invalid responses, preapplication HTTP rejection, mobile edits/deletion, target archiving between batches, accounts/settings/disabling/token, two workers, backoff, intent rollback, v4→v5 upgrade. Two actual container child processes crash before request/after fake API commit; recovery never repeats writes. Lifespan worker finishes queues without new browser requests. Database deletion race checked: runtime mode=rw never creates an empty replacement. Real HTTP/auth/resume and preservation of financial data/authorization after recreation passed. Test resources removed; no real API/database/bank. Details: docs/delivery.md.
- B4: same Docker/Compose/linux/arm64/CPython versions: **292 passed**, 57 new checks. Network-isolated SDK MockTransport: all entity kinds/raw/exact amounts, diff/cursor/soft-hard deletions, mobile edits, absent UUID, failures/retries/atomicity, generation/token races, cache/freshness/restart, capture during SDK failure, threshold 2/3/5, splits/N:M/exclusions, manual rules, viewed=false, payees/directions, started payloads. v3→v4 upgrade/rollback with backup; HTTP/auth/resume/preservation after recreation passed. Resources removed; no real API/database. Details: docs/mirror-predictions.md.
- B3: same platform/runtime: **235 passed**, 66 new checks. Concurrent commands/settings, canonical accounts, lease/epoch, repeated/reordered pages, duplicates/totals/allowlists/completeness, empty intervals, gaps/overlap/one-time reread, six months/leap/year boundaries, disabling/one-account failures, restart, v2→v3 upgrade/rollback. Actual Docker HTTP: save partial job, recreate backend, repeat command/page, complete remaining intervals. Resources removed; no real bank/API/database.
- B2: same platform/runtime: **169 passed**, including 76 new storage/migration checks. Synthetic legacy v12, every SDK entity/exact JSON, 1:N/N:M groups, exclusions, old UUIDs/attempts, edited/deleted records, BLOB/unknown-schema archives, WAL, repetition, errors/races/rollback. Test-volume CLI/health and preservation after recreation. Containers/volume removed; no actual API/production database.
- B1: same platform/runtime: 93 passed, network-isolated config container, HTTP/auth, backend recreation preserving database identity. Containers/unique test volume removed. AMD64 not yet checked; that is B6.
- One Starlette httpx TestClient warning remains; tests pass. SDK retains httpx 0.28.1. Changing the test client/dependencies was outside B1 extraction.
- ZenImport: scripts/test-contracts.sh, Docker/CPython 3.12.15, 56 passed; new contract only, not full application regression.
- ZenMoneyAPI: scripts/test.sh, Docker/CPython 3.12.15, 244 passed, strict mypy for package/typing_contract, 99% line coverage. Test containers have no network.
- Vendored SDK SHA256 b28722f6ee09ea79726cbab64fa769171904b6c9a2e6b825bc2d2c88437e1e58; all nine files match SDK source.
- Real API/bank/mobile UI not tested in these stages. Production database not opened for changes.
- Preexisting untracked SDK .idea/ was excluded from commits.

## Contracts for later stages

docs/sync-api-v1.md defines routes/errors, amount/time formats, versioned settings, idempotency, capture leases, interval completeness, independent delivery. B2/B3 wire contract unchanged. storage.transaction enables FK/recursive_triggers; records.save_record preserves UUIDs/versions. Settings/planning/completeness/allowlists, B4 learning/mirror, and B5 sender are implemented. Details: docs/mirror-predictions.md and docs/delivery.md. B5 wire contract is also unchanged.

## B2 foundations

- Structure/conversion command: docs/financial-storage.md. Runtime upgrades own v1/v2/v3/v4 to v5; legacy v12 requires explicit conversion into another file. Real rehearsal: M1.
- Legacy mappings stay in account_bindings; accounts unconfigured. Reconfirm profile/period/selection. Never invent coverage: coverage empty, last_complete_end NULL.
- bank_processing/processing_receipts are independent of the mirror. B3–B5 must not reset them on bank updates/page retries. Old audit confirmations count too.
- Prepared never-sent payloads rebuild through the SDK where possible with original UUID/viewed=false. needs_rebuild=1 blocks attempts until safe preparation. All started/uncertain payloads/UUIDs are retained; uncertain never authorizes another write.
- Legacy existing-record edits are archived only. category_evidence remains empty until B4; eligible new-record manual categories are separate.
- HTTP/settings/capture were absent after B2, then implemented by B3. B4 adds refresh/prediction, B5 delivery. Real bank/API/mobile UI and AMD64 were not tested.

## Historical B3 → B4 handoff (completed)

- docs/capture.md. Runtime/converter create v3; own v1/v2 upgrade with backup, source legacy v12 converts only explicitly to another file.
- Catalog/target validation initially read mirror only; service_meta.zenmoney_refreshed_at uses Unix seconds, maximum age 300. B4 must refresh and set freshness atomically with successful diff/cursor. Never call real SDK inside sync_api.run with an open transaction.
- job_accounts stores settings/target/authorized_start; job_context connector snapshot. captured_records references exact bank_versions; coverage only after complete. New incomplete versions cannot deliver.
- Account locks/commands/leases persist. Paused retains locks; next click resumes; stop releases capture only, not complete-interval delivery. Newly enabled accounts join next job; disabling immediately cancels their incomplete intervals.
- reimport_ranges preserves remaining one-time work regardless of old coverage, by settings version. Inclusive periods, connector window, maximum 10,000 intervals/job. Future popup computes presets; server accepts explicit import_start.
- Settings changes mark unstarted prepared needs_rebuild=1 without UUID replacement; old job snapshots never change. Uncertain blocks target changes. B5 rechecks enabled/version/target/journal before every intent.
- delivery_state stays waiting; neither capture nor test freshness proves real delivery. M1/M3 and AMD64 remain ahead.

## Historical B4 → B5 handoff (completed)

- docs/mirror-predictions.md: current mirror/prediction contract, schema v4, own-v3 upgrade/backup/rollback verified. Old raw/cursor/UUIDs/attempts/groups retained.
- Mirror.refresh(force=True) before each send outside SQLite transactions, then enabled/version/target/complete-interval recheck. B4 never sends or treats prediction presence as authorization.
- predictions.predict(..., revision=...) takes specific captured_records bank_versions; preliminary category_predictions uses current version. Newer incomplete versions never replace authorized ones.
- Available UUID proves existence after mobile edits; explicit deletion suppresses forever. Absence never authorizes uncertain retries. Started payload/UUID immutable.
- Three independent viewed=true payments, one category, no conflict; our viewed=false never trains. Manual choice → rule → history → NULL. Refresh recomputes predictions without updating delivered records.
- New jobs force SDK refresh; catalog/new enabled settings reuse freshness up to 300 seconds. SDK failure allows capture, blocks delivery. No background sender/timer yet; that is B5.
- SDK HTTPError lacks Retry-After; read retries limited to one after 0.1 seconds. Initial diff requires explicit account/instrument/tag/transaction; M3 verifies actual empty accounts. Real data/API/mobile UI and AMD64 still untested.

## Historical B5 → B6/E5 handoff (independent portion complete)

- docs/delivery.md. v5 adds delivery_authorizations/delivery_schedule; FKs/triggers prove complete versions/freeze basis after attempts. Own-v4 private backup/rollback verified; migration never authorizes legacy rows without full bank evidence.
- POST jobs/complete atomically authorize eligible complete versions and wake lifespan worker. Exact commands preserve UUIDs. Worker continues after browser/server restart; no timer-created capture jobs.
- Interprocess flock on real database path plus SDK/cursor RLock. Network outside SQLite write locks; UUID/exact payload/intent/ever_attempted persisted first. Runtime transactions open only existing mode=rw databases; bootstrap is sole creator.
- Force mirror before batches, then enabled/version/target/source/complete-version checks. New incomplete versions never replace authorized ones. Disabling after intent completes only that batch; stop never revokes complete intervals.
- SDK expense/income only, viewed=false, B4 category/NULL. Skip processed/excluded records; similar unknown UUIDs do not prove links. Mobile edits/deletions final.
- Atomic diff application; remaining UUIDs checked with read_diff(forceFetch transaction). Crash attempting → uncertain, never upsert. UUID absence/HTTP 400 never prove nonapplication. Even intent-before-request crashes may safely block unknown; no reset/retry-write button.
- Reads only retry, with persisted 15…300-second cycle delays. No SDK Retry-After. JobView distinguishes running/waiting/blocked/complete and four counts; full success requires complete capture/all processing.
- At this handoff B6/AMD64/E5 were untested. Need E1–E5/M1–M3 before LIVE; no real API/production/mobile viewed=false validation. SDK/production unchanged.

## B6 → E1 handoff

- Independent backend ready for new Chrome v1 development; joint acceptance remains E5. Never restore financial editors/bank injected UI. SDK stays separate/unchanged.
- scripts/test-platforms.sh is the actually executed ARM64/AMD64 suite. Still one process/local SQLite volume. AMD64 emulated; no physical x86 server/external HTTPS.
- backend.snapshot export/restore validates current own schema and publishes only a new destination. Snapshots never replace journals/prove absence of later writes. Real copies/migration belong to M1.
- docs/operations.md and docs/b6-validation.md record facts. Production server/extension/data unchanged. Next E1 in neighboring independent ZenImportChrome with history preserved per docs/refactor/03-extension.md.

## Entry template after the next stage

- Stage / date / project / commit:
- Changes and locations:
- Checks and results:
- Not checked:
- Contract changes:
- Next specific stage:

## E4 — October 16, 2026

Popup: one Synchronize/Stop capture button, brief result, collapsed server/token settings, compatible accounts/explicit Do not import, 30-day/six-calendar-month/custom dates, one-time old-history capture. No ZenMoney token in Chrome. New unconfigured accounts do not block configured accounts (backend clarification, no wire v1 change). None configured/all disabled prohibits start. Chrome Docker: 12 unit passed. Backend/browser regressions started; final results in E5.

## M1 — October 16, 2026

Completed offline real-schema-12 rehearsal on a consistent read-only-volume copy. All 561 bank records/539 groups/32,672 entities/71 jobs/140 attempts preserved/content-verified. Full lossless archive: 58,651 rows; repeats/restoration identical. Four manual categories/four exclusions preserved. docs/migration-rehearsal.md. Added export-legacy/offline verifier; 352 ARM64 tests/lifecycle passed. Production source/API/extension not switched. M2 ongoing, M3/M4 await live acceptance.

## M2 and popup 0.1.1 — October 16, 2026

Checks complete on synthetic data/real restored copy without networking: backend 352 ARM64 + 352 emulated AMD64; Chrome 13 unit + nine browser/integration (three popup/six integration). Financial copy fingerprint unchanged after startup/stop. Fixed native popup shrinking 115 → 390 px with regression. Backend docs/m2-validation.md, docs/live-acceptance.md; Chrome docs/testing.md. M3/M4 need live acceptance; production unchanged. SDK unchanged; no pushes/tags.

## Production startup with a clean database — October 16, 2026

The user explicitly declined migration and authorized stopping the legacy backend. Old container stopped/autostart disabled; new localhost:18792 uses a separate clean volume. Read-only actual mirror, api_version=1/ready, 60 eligible accounts. Bank records/jobs/attempts initially zero. Fixed Instrument.shortTitle in catalog/prediction and actual-format fixtures; 353 Docker checks passed. docs/local-deployment.md explains connection/old-history reimport risks without receipts. User deferred mobile acceptance; M3 not marked complete. Secrets stay private/ignored; no pushes/tags.

## New transaction chronology/category provenance — October 16, 2026

Fixed created to use authorized immutable bank-version time; changed stays send time. Existing attempts/payloads/UUIDs unchanged. Tests cover different same-day batch times, UTC midnight, original version time despite newer bank data, no resending, and unconfirmed ZenMoney-response categories. Full ARM64 scripts/test-docker.sh: 357 passed; HTTP/auth/SIGKILL/graceful/recovery passed with isolated networking. After local network pool exhaustion, removed one empty old zenimport-check-83481 network; rerun succeeded.

Read-only production diagnostics found identical created for two differing bank times and tag=NULL in both requests; first ZenMoney delivery responses already contained categories. Local learning/rules/manual choices empty. No remote category clearing/overwriting or actual-record experiments.

Built/updated the local runtime after confirming no active captures/pending sends. Health 200, original 518 payloads/1036 attempt events preserved; checked today's payloads unchanged. No schema/SDK/API v1/extension change. Future mobile chronology needs the next user synchronization, not fixture evidence. No pushes/tags.

## Discrepancy view — October 16, 2026

User-requested read-only extension-page API only: GET /api/v1/connections/{id}/discrepancies. Enabled accounts/current connection/proven bank days; queues/uncertain delivery/shared targets warn. Known UUIDs remain linked after mobile edits; unmatched records consider exact amounts, purchase splits, balanced settlements. Remainders are candidates without stored links/financial actions. Migration/SDK unchanged; docs/discrepancies.md.

Full ARM64 Docker: 370 passed; HTTP/auth/SIGKILL/graceful/recovery passed on synthetic data. Updated runtime after confirming no active sync. Actual report GET 200: 184 covered days, zero incomplete days, 132 groups/500 candidates; counts do not prove each record wrong. Read checks added no attempts and created/edited/deleted no ZenMoney records.

### October 16: historical duplicate investigation

Read-only confirmation: October 14's three bank records match three old and three new ZenMoney UUIDs. New backend journal: 518 intent/518 response_applied, no repeated intents. Agreed clean startup lost old links and created new UUIDs for existing history.

Added unique legacy bank-reference recognition before first send with permanent receipts and no mobile overwrites. Unknown old intents never authorize UUID reassignment. Date/amount similarity still does not prove identity at this checkpoint. Read stopped old volume for a private cleanup plan; no financial deletions. Docker: 384 tests and HTTP/auth/restarts/crash/graceful checks pass; final-check/deployment result below.

Final scripts/test-docker.sh: **384 passed**, HTTP/auth/SIGKILL/graceful/snapshot restore. Rebuilt local runtime; no active capture/authorized unfinished delivery before restart. Health 200, still 518 intent/518 response_applied, no extra sends/deletions. Private read-only plan: 493 pairs over 132 days, including 490 unreviewed copies and three reviewed October 14 copies matching retained originals' categories/payees. Separate HTML retains exact keep/remove UUIDs.
Other suspicious records/composite groups were excluded from the bulk plan. Real financial deletion awaits explicit approval of that exact list.

### October 16: approved cleanup and day/amount matching

The user explicitly authorized deleting 493 planned new copies and restoring account/day/amount matching. Before delivery, prioritize known UUIDs/bank references, then account/date/direction/instrument/exact amount. Repetitions match by time/multiplicity; create only positive differences. Occupancy is bank-side: a transfer can match one bank expense/another bank income. Shared targets with ambiguous physical-account ownership disable amount matching. day_amount_order receipts never train categories.

Manual backend.duplicate_cleanup is separate from HTTP/jobs. It checks approved plan hash, fresh keep/remove snapshots, and journal ownership, saving a private SQLite copy/fsync intent before API calls. Reusing a journal is read/verify only. Canonical origin=duplicate_cleanup single groups retain originals for processing/learning; retired-copy deletion never suppresses originals, while later original deletion stays final.

Docker **402 passed**, including 21 deletions batched 20+1, lost responses before/after application without repeat writes, stopping on mobile edits, approved UUID protection, equal-amount 1:1/2:2/3:2/2:3 in both directions, repeated commands, shared targets, both transfer sides. Full lifecycle/actual cleanup results follow.

Actual October 16 outcome: **493/493 copies deleted, 493/493 originals preserved** without significant field changes, under the explicitly approved SHA256 plan. Twenty-five read-confirmed batches, no repeat deletes. Private plan/predeletion SQLite/fsync history remain in persistent storage. Private HTML in data and outputs/refactor-handoff now marks completion.

Updated Docker backend: health=200, SQLite integrity/FK=ok. 493 retired deliveries suppressed, all 518 bank records delivered (493 canonical groups point to originals). Original history unchanged: 518 intent + 518 response_applied, no new creation. Learning restored from originals: 448 eligible examples instead of unreviewed copies. **October 14: three bank/three ZenMoney transactions**.

Fresh read-only report: seven candidates across October 5/2, September 6, May 12/10. Outside the approved 493 pairs and not deleted by guesswork. Full Docker **402 passed**, HTTP/auth/SIGKILL/graceful/snapshot. Old service remains stopped. Local changes, no pushes/tags.

### October 16: all types and remaining composite differences

Read-only actual-mirror diagnosis: seven report rows form five economic groups corresponding to eight new copies. Two single matches are transfers; others a split, combined fee, and settlement. A separate read-only run recognized all eight bank/seven original ZenMoney records without journal changes. This is actual diagnosis, not production-database automated testing. No creation/edits/deletions in this task.

Future first sends use signed target-account movement/date/instrument/exact amount regardless of type. Description references establish priority, not an amount-match veto. Added unique persistent split/settlement groups with bounded combination search; overlaps/ambiguity/exhaustion never choose arbitrarily. Started deliveries never reassign; mobile edits/deletions final.

Full scripts/test-docker.sh: **427 passed**, HTTP/auth/SIGKILL/graceful/snapshot with synthetic data/fake API. All transfer/debt directions, large exact amounts, splits/merges/settlements, ambiguous groups, no recreation after mobile edits/deletion. Schema/SDK/Chrome unchanged. Existing eight copies not deleted by this change.

Updated local runtime after verifying zero active captures/authorized unfinished deliveries. Health=200, integrity/FK=ok, 493 prior groups preserved, still 518 intent + 518 response_applied. Container matching-module hash equals source. Applies to future first delivery; eight existing copies not reassigned. Local commit only, no pushes/tags.

### October 16: authorized cleanup of eight composite duplicates

The user explicitly authorized removing eight verified copies, preserving seven originals, and synchronizing again. Extended manual duplicate_cleanup for agreed single/split/settlement groups, exact signed target-account totals, full keep/remove snapshots, independent identifiers, and confirmed-delivery ownership. Mirror retains canonical UUID sets so retired-copy deletion never suppresses retained originals.

Full Docker **442 passed**, HTTP/auth/SIGKILL/graceful/snapshot with synthetic databases/fake API. Local runtime rebuilt/updated before actual cleanup; schema/SDK/extension unchanged.

Actual cleanup used the approved private SHA256 plan: **8/8 removed, 7/7 originals retained**, one batch, no repeated financial requests. Predeletion SQLite/plan/fsync journal remain in /data; no personal data in Git. Independent read: 518 bank records across 184 covered days, zero unchecked days/extra candidates/daily total differences. Period movements match; cleanup created no delivery attempts. Repeat bank capture/evening-record checks are next; session expired and user was asked to reopen history.

### Post-cleanup resynchronization: acceptance status

After the user reopened history, popup created a job but capture_context_changed ended the connector after 32 seconds, before page one. No new bank records/delivery: still 518 intent + 518 response_applied. Fixed tab readiness in independent ZenImportChrome 0.1.8, local a79c760; 23 unit, nine browser, 16 integration passed in Docker. Await another user bank login/live run with the updated extension. Cleanup 8/8 and preservation 7/7 remain proven; two evening records not recaptured/sent yet.

### Final live synchronization after cleanup — complete

October 16 at 21:36 (UTC+7), after user sign-in, Chrome 0.1.8 read 67 records for October 2–16. Exactly two missing records created/confirmed in ZenMoney. Latest capture/delivery complete; all 520 bank records delivered. Journal 520 intent/520 response_applied; original 518 never resent.

A separate manual read-only audit after fresh incremental read_diff found 520 bank records represented by 519 ZenMoney records for April 16–October 16 (splits/merges explain counts). 184 covered days, zero unchecked days/extra candidates/missing links/single or composite amount mismatches/daily differences. Eight copies absent, seven originals preserved without significant field changes. Integrity/FK=ok.

Private full result: data/composite-cleanup-after-resync-20261016.json, outside Git. Period bank/ZenMoney movements equal. ZenMoney balance equals available bank balance; bank current balance is higher by the displayed held amount. Initial history/holds were not altered to force equality. Actual Chrome discrepancy page/popup verified. This is separate live acceptance, not automated production-database tests. Local changes, no pushes/tags.

### Full review fixes — October 16, 2026

Fixed B-01: occupied transfer sides now derive from immutable actual matching accounts, not mapping history. Schema 6 adds processing_targets, atomically persisted by matching/delivery/manual cleanup. Migration reconstructs old targets only from saved requests/eligible confirmations. Current mappings/mutable ZenMoney fields are not evidence; initial duplicate-delivery confirmation cannot establish retained originals' accounts in old cleanup groups.

If a historical link's actual side cannot be proven, new records for that account/day remain pending with existing_ownership_unknown; other days continue. This also covers candidates beyond the first 20-record batch. Potential duplicates are not automatically created in this case.

Updated the SDK to verified local wheel 0.6.1 with fixed OAuth transport lifetime; checksum/dependency checks updated.

Full ARM64 Docker: **454 passed**, HTTP/auth/persistent restart/post-send SIGKILL/graceful/snapshot. Recovery uses original UUIDs without repeat writes. Chrome 0.1.9 joint run: **17 passed**. Twelve new side-ownership regressions are included in 454, not added separately.

Only synthetic data/fake API. Production container not updated/database not migrated. Changes prepared locally under /review; no commits/tags/push at this checkpoint.

### Remote migration rehearsal preparation — October 17, 2026

Full-review fixes saved as separate local SDK/backend/Chrome commits. Added explicitly invoked A/B lab with separate Compose/image/loopback ports/API tokens/database directories, never mounting production Compose/container/volume. Ordinary runtime excludes the live runner.

Pinned test identity from prior private SDK acceptance and fresh API reads. Test token differs from production; local source checks precede client opening, owner checks precede writes/worker startup. Created only three VND/VND/USD test accounts, two categories, one payee; original entities/two transactions retained. Fsync intent before requests, independent verification, flock; repeats check old UUIDs only, never recreate catalog.

A/B sequentially verified health/ready, three new catalog accounts, schema 6, empty bank/job/delivery/attempt/link tables. Stopped afterward; private state stays in .local/.

Full ARM64 Docker: **475 passed**, including 21 new tests; HTTP/auth/SIGKILL/graceful/snapshot. Fake-API automated checks and live catalog acceptance are separate. Migration/empty-database duplicate prevention, popup, HTTPS remain next, not yet claimed. Commands/criteria: [migration-lab.md](../migration-lab.md). No push/tags/publication/production changes.

### Controlled history and A/B switching — October 17, 2026

Continued isolated acceptance using only the previously verified test user. Two independent sets: 36 seed records covering all supported types and bank fixtures of 21 records each (later 23). Expense/income/refund, equal amounts, four debt directions, both ordinary/currency transfer sides, split/merge/mixed settlement. Initially two missing records per set; others already in ZenMoney.

**Twelve live jobs** through actual backend HTTP. Each A created exactly two missing records; repeats wrote nothing. Copied-snapshot B created zero old records, preserved API category/comment and expense→debt edits and one deliberate deletion; two subsequent new records created once. Physically empty B with a new instance_id recognized independent history without old creation; two later new records created once. Every repeat made zero writes. Eight expected backend UUIDs, no unexpected writes; original account transactions unchanged.

Six checkpoints verified exact UUIDs/fields, new categories/viewed=false, daily gross/net, balances/debt ledger, integrity/FKs. Deliberate 503 VND deletion explicitly excluded from the migration set's expected bank net. Transport metadata records write IDs before HTTP. Initial run fixed only the controller: wait for temporary uncertain during running, never retry final unknown. Resume retained job/request_id, no new write.

Full ARM64 Docker **489 passed**, HTTP/auth/restart/SIGKILL/graceful/snapshot; 35 lab checks included. Production backend/database/ZenMoney unchanged. Lab instances stopped/network removed; private logs/database archives retained outside Git. Scenario local commit 09cef20. No pushes/tags/publication.

Acceptance covers SDK + backend HTTP + SQLite, not Chrome popup/DOM/HTTPS/mobile UI. The empty set lost no deletion history before first import and cannot prove arbitrary database resets safe. Transfer existing databases to preserve prior deletions/mobile edits. Results/commands: [migration-lab.md](../migration-lab.md).

### Chrome and HTTPS: isolated migration acceptance — October 17, 2026

Neighboring ZenImportChrome completed popup A HTTP → B HTTPS switching for copied/empty databases. Bank synthetic; MV3, native permissions, TLS without certificate bypass, backend/SDK/test ZenMoney actual. Twelve live jobs, six expected UUIDs, zero repeat writes. Categories/viewed=false/exact records/daily movements/actual balances/integrity matched; baseline test data preserved. Permission/401/certificate failures handled. Independent scenario also completed 12 jobs; Chrome Docker regressions: 31 unit/17 browser-integration passed.

Production data/container/backend code unchanged; this commit updates the report only. Fixture/fixes live in Chrome, 929fa32/e8da2e2. Scope/empty-database limits: [migration-lab.md](../migration-lab.md). Public server/mobile UI untested. No push/tags/publication.

### SDK/GitHub and server Docker preparation — October 17, 2026

User clarified SDK distribution **zenmoney-python**, repository `vaslv/zenmoney-python`, import `zenmoney_api`. MIT approved/copied from the created GitHub repository. SDK 0.7.0: wheel/sdist/SHA256SUMS, strict Twine, 260 tests/mypy, 99% coverage. Local history merged with initial GitHub license without rewrite/push.

Backend depends on `zenmoney-python==0.7.0`, installs a separate wheel with SHA256/require-hashes/no-index/no-deps and pip check. Fixed prior pyproject 0.6.0 versus Docker wheel 0.6.1 mismatch. SDK source never copied. Before first release, use verified local wheel; afterward explicitly download release artifacts and update with scripts/update_sdk.py.

Added image-only compose.server.yaml, private host-Python-free .env bootstrap, OCI metadata, AMD64/ARM64 preparation, Caddy example. Each architecture passed **492 tests** plus HTTP/auth/SIGKILL/graceful/consistent snapshot restoration without repeat writes. ARM64 native, AMD64 emulated. Separate image-only acceptance verified mode-600/no-overwrite bootstrap, runtime layout/SDK, localhost binding, read-only root/UID, OCI revision, volume preservation after recreation. Caddy validated normally without networking/user certificates.

Prepared MIT/CI/instructions for all three projects. Workflows create no tags, publish no packages/images, run no real financial tests. GitHub Actions had not yet run. Gitleaks 8.30.1 found no secrets in full previous history; separate known-local-token checks clean. Production backend/database/ZenMoney neither used nor updated.

Publish SDK commits/release first, then backend with verified SDK, then Chrome. [Procedure](../publication.md), [server](../server-deployment.md). Local only; no push/tags/GitHub Release/PyPI/GHCR/Store at this historical checkpoint.

### English documentation and messages — October 17, 2026

Translated documentation, code comments/docstrings, configuration comments, synthetic example labels, and application messages into English. Historical stage results, dates, identifiers, and limitations are preserved. The production container/database and already published SDK release artifacts remain unchanged.

Docker on Linux/ARM64: **492 tests passed**, plus HTTP/auth, persistent recreation, SIGKILL recovery, graceful shutdown, and snapshot restoration without repeat writes. Extension integration against this backend: **17 passed** with synthetic data/fake ZenMoney only. No push, new tags, or publication.


## 2026-10-17 — Published PyPI SDK dependency

- Replaced the checked-in prerelease SDK wheel with public PyPI installation of
  `zenmoney-python==0.7.0`, SHA256
  `529cd89ce941ddfabbe8a08c4ab5c587389680cda0e0b9b1f1abaf3e5f2734b7`.
  The version JSON, downloaded wheel, and GitHub release manifest agree.
  The SDK runtime modules match the previously used wheel; no financial behavior
  or database schema is changed.
- Docker enforces the wheel hash/version and binary-only installation without
  dependency re-resolution, then runs pip check. No PyPI credentials are required.
  Removed vendor code/cache; the offline updater now writes only the SDK lock.
- Updated dependency documentation and installed-package/update-pin tests.
- Docker: 492 tests passed on native Linux ARM64 and emulated Linux AMD64;
  HTTP/auth, persistence/recreation, SIGKILL recovery, graceful in-flight shutdown,
  and consistent snapshot restoration passed on both. Image-only deployment also
  passed on both, including actual CPU architecture, private bootstrap/no overwrite,
  UID/read-only root/loopback binding, SDK, OCI revision, and volume persistence.
- Tests used isolated generated projects and fake financial data only. Production
  containers, SQLite, and live bank/ZenMoney sessions were not used or changed.
  Two unused historical test networks were removed to free exhausted Docker IPAM
  capacity; no network with attached containers or production volume was removed.
- PyPI upload credentials remain outside this backend. Source push, Git tags, and
  registry publication are separate actions; this checkpoint is local only.


## 2026-10-17 — Manual container publication pipeline

- Prepared a manual GitHub Container Registry workflow for backend 0.1.0,
  with reusable two-platform CI, short-lived `GITHUB_TOKEN`/packages-write access,
  fixed-version and source-SHA image tags, and no automatic publication on push.
- Guarded existing version images against overwrite, recorded the registry digest,
  checked both platform manifests, and added post-publication image-only smoke
  checks. These steps are prepared, not claimed as executed on GitHub yet.
- Updated public deployment/publication instructions and recorded the actual local
  evidence in [pypi-container-validation.md](../pypi-container-validation.md).
- actionlint passed; source/history secret scans and focused manual review passed.
  No source push, Git tag, registry publication, or production restart occurred.


## 2026-10-17 — Linux CI bootstrap ownership fix

- User authorized backend source push and GHCR image publication; source commit
  `6ad950f` was pushed without creating Git tags or publishing other projects.
- GitHub AMD64 passed all application/recovery tests, but the image-only check
  exposed a test harness ownership assumption hidden by macOS bind mounts:
  the host-created mode-600 `.env` cannot be read as runtime UID 10001 on Linux.
- Run the three read-only bind-file verification commands as the host UID/GID,
  just like the bootstrap helper. Keep mode 600 and the service UID unchanged.
  No application, financial data, storage schema, or delivery policy changed.
- Stop obsolete workflow runs before retesting/retrying publication. The failed
  verification gate prevented publication of that source revision.


## 2026-10-17 — Native ARM CI and immutable artifact revalidation

- Published backend image 0.1.0 from source
  `60e5d24c6d4398c76f0ee9a0535e4bcd4a5f0d99` to GHCR at digest
  `sha256:77747ac1ddf90ea12ebcfd7d4202f67660bc3375a5ffafa508bfa82392fdfd0b`.
  Registry metadata and both architecture pulls are publicly accessible without
  authentication; the installed SDK and OCI source revision were checked locally.
- Both published architecture images passed complete local image-only checks.
  The first GitHub post-publication ARM smoke check hit its one-minute readiness
  deadline under QEMU. A parallel ARM CI run also exceeded process-exit deadlines;
  another run passed. Preserve the artifact and timing assertions instead of
  overwriting its version or silently declaring the failed workflow successful.
- Move GitHub ARM tests and image acceptance to `ubuntu-24.04-arm`, with AMD64 on
  `ubuntu-24.04`. Keep QEMU only for the multiplatform image build. Add reusable,
  manual-only **Verify published image** with validated digest/source SHA inputs,
  no publication permissions, and anonymous pulls on both native architectures.
- Allow the image check to assert an explicit expected OCI source revision while
  using a newer test harness, and retain service logs on failure before cleanup.
  No runtime source, installed dependencies, database policy, or image bytes change.


## 2026-10-17 — Backend GHCR publication accepted

- Backend source and container 0.1.0 are public on GitHub/GHCR. Runtime source
  remains `60e5d24`; immutable registry digest is
  `sha256:77747ac1ddf90ea12ebcfd7d4202f67660bc3375a5ffafa508bfa82392fdfd0b`.
  Published bytes and version tags were not replaced after the emulation timeout.
- Native backend CI on `b18af65` passed both complete 492-test/platform suites:
  https://github.com/vaslv/zenimport-backend/actions/runs/39029767213.
- Separate anonymous digest-based acceptance passed on native AMD64 and ARM64:
  https://github.com/vaslv/zenimport-backend/actions/runs/39029794475.
- Verified public registry manifest/digest, anonymous pulls, installed SDK,
  architecture/OCI source, and complete published-image deployment locally too.
- Added [release/installation report](../container-release-0.1.0.md), preserving
  initial failed/cancelled run history and the distinction between artifact source
  and verification-harness revisions. No Git tags, other-project publication,
  production restart/data changes, or actual remote-server deployment occurred.

## Popup data migration — October 17, 2026, local 0.2.0

Added authenticated ZIP export/import/status/resume API using the current schema
and a complete SQLite snapshot, not a merge or executable SQL dump. The manifest
records format/schema, instance, user set, size and SHA256. No environment credentials
are exported. Strict two-member ZIP, bounded compressed/unpacked sizes, integrity,
foreign-key, identity, and owner/schema checks precede restoration. The existing
2 MB API-body limit is unchanged outside the 64 MiB raw-ZIP import route.

A request/worker gate and writer lock prohibit exporting through an in-flight
financial request. Source pause is committed before snapshot and survives restart.
Resume is explicit. Import only replaces a destination with no prior sync/history;
discovery/catalog alone is allowed. Prior imports are final except idempotent retry
of the same digest before its first explicit sync. Import uses SQLite backup's
destination transaction; no live-file replacement/WAL deletion. Restored delivery
stays on hold until an explicit sync, resets its mirror for a current full read,
and retains receipts, deletions, ownership, UUIDs, and uncertain attempt history.

Fresh native ARM64 Docker: **508 tests passed** (492 existing + 16 migration cases),
HTTP/auth, read-only root/UID, real worker restart, SIGKILL, graceful stop, and
snapshot restoration passed. Migration cases cover corrupted/checksummed foreign
archives, paths/duplicates, wrong identity, size/auth, concurrent financial write,
post-sync prohibition, persistent pause, receipts/deletions, exact retry, and an
uncertain committed UUID without repeat writes. Expected warnings: upstream
Starlette/httpx deprecation and deliberately duplicated ZIP entry in a negative case.

Built-extension/actual HTTP backend browser acceptance also verified ZIP download,
address switch, upload, restored mappings, and no duplicate or recreated deletion.
All banks/ZenMoney/data were synthetic, in isolated Docker. No production database,
container, installed extension, real API, push/tag, or publication was used.
New AMD64/native remote HTTPS/mobile acceptance is not claimed by these runs.
Published backend 0.1.0 remains unchanged. Guide: [popup-migration.md](../popup-migration.md).

## Published 0.2.0 — October 17, 2026

With explicit user authorization, pushed backend source/documentation, created
and pushed Git tag v0.2.0 at 36694047a926cc231ca9e7731458df7f88b7c8ef, and ran GHCR
publication. Native AMD64/ARM64 CI and the full guarded publication workflow passed:
508 tests per architecture, lifecycle and image-only deployment, followed by public
anonymous pull/install checks on both native architectures. No real financial data
or production deployment was used. Existing 0.1.0 bytes were not overwritten.

Public 0.2.0 immutable digest:
`sha256:4887d92e74ecee5454a5c84bc15ff586b8879849ce53006f05b56e63eff48d9f`.
Independently verified anonymous registry manifest SHA256/platforms. Runtime source
is the tag above; subsequent documentation commits do not replace that artifact.
Run links, boundaries and installation: [container-release-0.2.0.md](../container-release-0.2.0.md).
Independent anonymous native-ARM64 pull additionally verified UID/SDK and all 29
runtime Python/SQL file hashes against the tested source, without data mounts.


## October 17, 2026 — BIDV policy (local 0.2.1)

- Added BIDV VND discovery/capture policy without changing API v1 or schema 6.
  Bank-specific detail fields remain isolated from VietinBank; equal source account
  numbers at different banks keep distinct identities. SDK/PyPI dependency is unchanged.
- Docker ARM64: 516 tests passed; HTTP/authentication, persistence, crash recovery,
  graceful shutdown and restored delivery checks passed on disposable fixture data.
  Logs: ignored `.local/bidv-full-tests.log`. No production service/data or ZenMoney
  account was used. Live BIDV delivery remains a separate acceptance step.
- Documentation: `docs/bidv.md`. Version metadata is 0.2.1, local/unreleased.
  No push, tag, publication, or production deployment is authorized by this feature request.

## October 17, 2026 — Named bank connections (local 0.2.1)

- Added a bank-filtered connection catalog, optional discovery names, rename and
  scoped account unlink endpoints. A bank can have multiple access profiles;
  their account memberships and capture jobs stay separate. Bank credentials
  are not accepted or stored. Chrome owns explicit profile selection.
- Reused canonical physical account IDs, mappings, coverage, and permanent
  delivery/deletion journals across overlapping access. Unlink invalidates only
  that connection's account capture; financial records and other access survive.
  Shared account import settings are intentionally global, not per access profile.
- Schema 7 adds only `connection_labels`. Owned schema-6 databases upgrade with
  a private backup. Verified 0.2.0 migration archives remain importable after a
  private temporary-copy upgrade; declared/actual schema mismatch is rejected.
  Archive restoration retains names, membership, account IDs and receipts without
  writing to ZenMoney. Existing migrations/backups remain transactional.
- Docker ARM64: 527 tests and full HTTP/authentication/persistence, SIGKILL,
  graceful shutdown, and snapshot/restored-delivery checks passed. Only synthetic
  databases and fake SDK responses were used. Logs: ignored
  `.local/profiles-full-backend-complete.log`. Earlier fixture-only assertion
  failures were corrected before this successful final run.
- API/storage guide: `docs/bank-connections.md`. Paired Chrome checks cover two
  artificial BIDV logins, separate targets/ignored accounts, returning to a login,
  overlapping access, repeat synchronization and a mobile deletion without writes.
  Real two-login acceptance is not claimed. SDK dependency is unchanged.
- Local source remains 0.2.1/unreleased. No production data, container, bank
  session, installed extension, push, Git tag or published image was changed.

## 2026-10-19 — Decouple discrepancy reporting from delivery

Reports now compare completed bank coverage with the current ZenMoney mirror
regardless of capture/delivery job state. Added bank_operations alongside the
existing ZenMoney operations list. Historical identities only break ties after
current account, date and signed amount validation; they cannot hide edited
amounts or dates. Removed daily-net balancing as evidence of a match, retaining
bounded unique same-direction split detection. No migration or delivery-policy
change; the report still never sends financial writes or creates links.

Validation: all 532 backend tests passed in isolated Docker, followed by HTTP,
persistence, kill/recovery, graceful-stop and snapshot-restore checks. Regression
coverage includes pending/prepared/uncertain delivery, missing bank counterparts,
changed linked amounts, mobile correction refresh and deleted remote records.
Two extension/browser integration tests also passed. Local runtime image rebuilt
and service updated; no push, tags or publication.

## 2026-10-19 — Recover independently captured transfer sides

Added conservative legacy transfer ownership recovery before ordinary matching.
A completed, authorized opposite bank capture corroborates the old movement and
its historical target; mutable mappings alone never establish ownership. Recovery
is revisited as new banks/accounts arrive. Each side retains its own target and
currency, including FX. Already claimed equal-amount transfers are excluded from
alternatives; genuine duplicates and changed mappings remain guarded.

Validation: 543 isolated Docker tests passed, including late second-bank discovery,
both transfer directions, FX, external unsynchronized counterpart accounts,
repeated full captures, mobile deletion, conflicting evidence and the equal-value
already-claimed transfer regression. Existing ownership/rebinding tests still pass.
Read-only live dry run identifies exactly the three user-confirmed legacy sides;
no production recovery or remote transaction write was performed by that dry run.

## 2026-10-19 — Validate live transfer recovery

The full isolated Docker suite finished successfully: 543 tests, HTTP/persistence,
crash recovery, graceful shutdown and snapshot restoration. Created a private
SQLite backup in the local data volume before updating the runtime; backup and
post-update integrity checks returned ok.

The normal worker recovered all three user-confirmed legacy transfer targets and
adopted their opposite bank legs, without direct edits to receipts. Each UUID now
has two distinct target claims and both bank records are delivered. Latest three
BIDV jobs are complete/complete, unresolved ownership errors are zero, and the
fresh discrepancy report has zero differences across 185 covered days. Delivery
attempts remained 1050, active mirrored transaction count remained 27383, and the
set of remote transaction IDs gained no entries. Live verification reused the
completed bank captures and refreshed ZenMoney; repeated full bank capture and
mobile-deletion safety were verified on synthetic Docker fixtures. No push, tags,
release or remote publication.

## 2026-10-19 — Prepare backend 0.2.1 for publication

- Kept the unpublished version 0.2.1 consistent across package/runtime/OCI metadata.
- Added CHANGELOG and a release/upgrade/rollback runbook. No published digest is
  claimed; 0.2.0 remains the latest published image.
- Reviewed the v0.2.0 delta for storage, matching, capture, migration and contract
  risks; no further backend defects identified.
- Docker ARM64 and emulated AMD64: 543 tests on each, plus HTTP/auth, persistence,
  SIGKILL recovery, graceful shutdown and snapshot restore passed. Image-only
  bootstrap/runtime/volume/OCI checks also passed on both architectures.
- Synthetic data only; no production containers, bank sessions or ZenMoney writes.
  Non-fatal upstream Starlette/httpx deprecation warnings remain. Local logs are
  excluded under .local/release-0.2.1/. Push, tags and publication were not run.

## 2026-10-19 — Publish backend 0.2.1

Authorized main/tag pushes and GHCR publication completed. Source 463f12c and
v0.2.1 identify the released runtime. Source CI 39260257528, tag CI 39260558371
and publication workflow 39260287161 passed, including native AMD64/ARM64 image
acceptance. Public digest and independent local ARM64 verification are recorded in
container-release-0.2.1.md. Production deployment/data were not changed.

## 2026-10-20 — Exact movement arithmetic found by local-mode L2 contracts

A synthetic common fixture proved that the former fixed 80-digit context could
round a remote amount into a false same-day/amount match. Added a bounded exact
sum/context helper, preserved both transfer sides, and used it in subset matching,
report totals/differences and reviewed duplicate-plan arithmetic. Sign checks no
longer multiply large decimals merely to compare their direction. Inputs outside
the resource span fail explicitly rather than rounding. No production records
were inspected or modified, and no deployment occurred.

Regression checks passed with an ambient precision of two digits and a 91-digit
fractional difference. Focused Docker tests: 85 passed. Final Docker unit suite:
553 passed, including the in-progress shared-contract adapter. Full Docker service
checks also passed authentication, SIGKILL recovery, graceful shutdown and restored
snapshot delivery. A subsequent sign-only refinement passed all 553 tests again.
This source-domain correction is committed separately from the L2 fixture harness.

## 2026-10-20 — Standalone extension L2 reference adapter

Added a manifest-pinned copy of the canonical Chrome c6e90aa business fixtures,
the Python reference dispatcher and deterministic fixture context. Eight pure
scenarios, 500 arithmetic vectors and 6,354 Unicode 15 vectors produce identical
results in Python/TypeScript Docker runners. Four later stateful specifications
explicitly reject unsupported execution. Added source/copy hash checks and unsafe
numeric-DTO controls. Details and commands are in docs/business-contracts.md.

Docker: 553 tests passed, HTTP/auth, SIGKILL recovery, graceful shutdown and restored
snapshot delivery passed; latest focused source refinement passed all 553 unit
tests again. No live API access, production database use, deployment, push, tags
or publication occurred. Next: L3 portable server migration and participant gates.

## 2026-10-20 — L3.1 format primitives (L3 remains in progress)

Added a private streaming format-2 codec and a canonical runtime definition, with
typed rows, all 49 table descriptors, strict UTF-8 key ordering, digest rules,
resource budgets and ZIP/CRC defenses. The golden format fixture is independently
checked in Python/Node and explicitly grants no activation authority. Existing
format-1 endpoints are unchanged; no new endpoint is exposed. Financial graph,
owner/credential screening, durable participants and protected handoff remain.
See docs/portable-format-v2.md. No production data/services or live API access.

### Browser storage parity checkpoint (L4, incomplete)

Added a shared generator for 1,044 actual Python archive-screening, classified
metadata, Decimal-text and mirror flag/amount observations, plus frozen Unicode-15
word ranges. Chrome now checks these outcomes in its native archive codec. The
reference suite recomputes the fixture; it cannot silently drift from Python.
The source-side nested JSON/surrogate/audit fixes remain separate earlier commits.

Validation: the full read-only network-disabled Docker test suite passed 737 tests
(34.39s), with synthetic data only. Contract copies pin Chrome 4690b92; all artifact
hashes agree. No production data, published images, tags or remotes changed.

### Shared complete financial fixture (L4 graph proof in progress)

Pinned the synthetic financial journal captured and delivered by DeliveryHarness.
The stored fixture has a complete independent Python graph/digest proof; it is
not the earlier deliberately invalid format golden. Missing-parent mutations of
all 33 populated FK cohorts are rejected. Focused Docker verification: 54 fixture
and graph tests passed in 3.13s. Chrome ce41164 implements only its read-only
relational subset so far; no financial activation or complete L4 claim is made.

### Stock legacy fixture parity checkpoint

Pinned the complete synthetic stock legacy conversion, including archived raw rows
and schema objects. Python independently validates its full graph and unchanged
financial digest; Chrome now checks the exact original legacy fingerprint without
executing archived SQL. Focused Docker fixture/graph checks: 55 passed in 3.62s.
Browser L4 still needs its remaining financial and lifecycle gates.

### Shared create-once history negatives (L4)

Added ten independent Python rejections for the same fixed synthetic journal:
missing/mismatched attempt, missing/wrong authorization, wrong payload identity,
active delivery and lost/contradictory terminal evidence. Focused Docker module:
13 tests passed in 0.71s. Browser validation remains read-only and incomplete;
these tests do not grant sending or migration authority.

### Exact shared mirror projection case (L4)

The same current fixture now tests a raw numeric amount of 1.00000000000000000001
with matching exact mirror TEXT and category position zero. Python accepts that
graph and rejects changing only the position. Focused Docker module: 14 passed
in 0.88s. Chrome's mirror proof is read-only; no API or production state changed.

### Shared bank wire and historical clock observations (L4)

Added actual Python model observations for 111 BankRecord inputs, five aware
microsecond timestamp pairs and 302 historical Vietnam clock projections. The
reference generator is pinned and recomputed in Docker, including observations
around every discovered offset change from 1800 through 1979. Browser helpers
match these outcomes; they are not yet a complete financial graph proof.
Focused Docker reference/financial suite: 15 passed in 0.94s. No live requests.

### Shared raw bank timestamp proof (L4)

Verified that an equivalent stored UTC timestamp remains valid without rewriting
the original raw bank record, while a one-microsecond mismatch is rejected.
Focused Docker financial fixture/graph suite: 67 passed in 3.78s. Browser raw bank
proof remains inactive and read-only; complete capture proof and lifecycle gates
are still outstanding. No production data or API calls were involved.

### Bound complete-page validation by actual stored evidence

A new isolated 256 MiB child-process regression reproduced MemoryError for
self-consistent archives claiming 10^12 or 2^63-1 pages without those pages.
The validator now compares the actual row count and ordered page numbers, without
constructing range(total_pages). Both cases fail with incomplete_coverage.
Focused read-only Docker graph/resource/fixture checks: 69 passed in 4.00s.
The pre-fix failure log is retained locally; no production data was accessed.

### Reject excessive JSON fanout before building its visit stack

A separate 256 MiB isolated child reproduced MemoryError when the decoder's
5,000,001-element list was expanded into visit tuples before checking the existing
5,000,000-node budget. The graph scanner now checks the known pending-node lower
bound before extending dictionary/list children. Oversized input returns the
existing json_budget_exceeded error. Focused offline Docker checks: 70 passed
in 4.46s, including the failing-before-fix resource regression. No live data used.

### Nested JSON screening boundary observations (L4)

Added eight actual Python expectations for nested surrogate text, decoded
duplicate keys and exact depth boundaries. Chrome now distinguishes an ordinary
syntax failure from fatal Unicode/resource decoder rejection when screening
nested JSON text. All shared artifacts are repinned to that source commit.
Focused offline Docker screening/resource/graph checks: 56 passed in 5.24s.
No production credentials, financial writes or publication were involved.

### Shared line-terminator lexical coverage

Expanded actual SQLite/Python vectors for trailing line separators in financial
scalars, identifiers and audit metadata. Existing browser validators already
matched the reference; no runtime correction was needed. The shared suite now
contains 2,642 SQLite CHECK outcomes and 1,132 value observations. Two focused
Docker regeneration tests passed in 0.21s. Contracts are repinned locally.

### Require actual empty-page evidence for complete intervals

A regression showed that an added complete interval with zero declared pages
and no page evidence was accepted by the archive graph, despite CaptureComplete
requiring 1–10,000 pages. The graph now enforces those same bounds. Genuine
empty history still uses one confirmed empty page. Focused offline Docker
resource/graph/fixture checks: 72 passed in 5.15s; the zero-page case failed before
the correction. Ordinary capture behavior and production data were unchanged.

### Complete archived-capture browser parity checkpoint

Added the compact actual-Python Unicode capture recipe: an 828,022-byte UTF-8
request expands to 2,434,555 stored ASCII bytes with a pinned SHA256. The ordinary
HTTP limit is unchanged. Independently verified dictionary-order/offset equality
and rejection of changed captured values on the shared fixture. Focused offline
Docker graph/resource/model/fixture checks: 74 passed in 5.28s. Chrome now has a
complete read-only facts validator; protected native activation/lifecycle and the
remaining L4 interop/performance/review gates are still required.

### Native browser financial archive interoperability (L4)

Added an explicit test-only interop script and Docker test-image copy. Python
archives for current and legacy graphs, plus a genuine format-1 export restored
into a compatible temporary backend and converted to format 2, pass through actual
MV3/IndexedDB staging/validation/copy in Chrome 140 and Chromium 145. Returned
archives install into isolated synthetic protocol-2 targets, re-export from SQL
and pass through the browser again. Python compares every logical scalar and
separately asserts the installer's declared runtime/provenance normalizations.

All prepare/install/final verification phases passed for the three graphs. Each
browser passed three native cases in each direction (145: 5.41s/4.39s; 140:
27.04s/21.64s). A first format-1 test expectation confused a physical hold-audit
alias with its original logical key; correcting that oracle required no runtime
change. Synthetic owner/participant setup is not a live identity proof or browser
activation gate. Test scripts never enter the runtime Docker image.

Full isolated Docker gate: 759 tests passed in 38.15s, followed by HTTP/auth,
fake-SDK dispatch, SIGKILL, graceful stop and snapshot restoration. Browser L4
authority/export/retirement and independent review remain incomplete. No real API
calls, production data, push, tags or publication.

### Shared participant receipt wire observations (L4)

Added 435 deterministic observations of the real Python participant receipt
verifier and 15 canonical-byte outcomes, including exact counters above 2^53,
null fields, UTF-8 key order, byte/depth boundaries and peer/operation binding.
The source regenerates the fixture independently; no production data or network
is involved. Docker vector/participant/HTTP checks: 53 passed in 2.24s. Native
Chrome 140 and Chromium 145 each passed every observation; browser unit checks
total 164. This adds test data only, not new backend authority or runtime logic.
The browser participant and protected activation remain unfinished L4 work.

### Actual native/SQLite authority exchange (L4)

Added an explicit test-only participant exchange script to the Docker tests target.
The runner in the neighboring Chrome project persists a genuine MV3 profile
between phases and exchanges source-seal, destination-arm and source-handoff
receipts with real SQLite participant transitions. Both directions activate once;
request replay returns the same receipt and awaiting-sync keeps ordinary workers
blocked. All 49 logical tables are compared with separately asserted Python
mirror, prediction, unattempted-source and provenance normalizations. Native
activation preserves its validated financial facts for later guarded first-sync
initialization; this does not enable local financial sends.

The seven prepare/browser/SQLite/verify phases passed on Chromium 145 and Chrome
140. The three native phases took 2.55/1.29/0.99s and 7.93/7.53/6.71s respectively.
Authenticated peer status and owner identity are offline private fixture ports;
these runs do not establish live HTTP authentication or real SDK owner checks.
The full isolated backend Docker gate passed 760 tests in 50.30s plus HTTP/auth,
fake-SDK dispatch, SIGKILL/recovery, graceful stop and snapshot restoration.

An independent read-only review found no actionable defect in the final authority
implementation and interop runner. Runtime backend code is unchanged; the script
is excluded from the runtime image. No live account, production data, push, tags
or publication were used.
# Local-mode L6 interoperability correction — 2026-10-21

- Portable mirror proof now applies the actual generic movement flag projection
  to reminder and reminderMarker rows as well as transactions. The SDK and mirror
  retain unknown fields; previously a valid viewed/hold field on either reminder
  kind made the backend reject its own exported mirror. Raw data is unchanged.
- Two synthetic regressions validate real mirror-to-archive graphs and reject a
  deliberately changed projected flag. The Chrome proof receives the same fix.
- Manual/API review confirmed the proof now matches mirror.save_entity; no SDK
  request, transaction writing, schema or portable-format change is involved.
- Docker: focused portable graph suite 54 passed; full isolated Python suite
  762 passed in 40.20s. Three existing deprecation/duplicate-ZIP fixture warnings.
  Logs: /tmp/zenimport-l6-backend.log and /tmp/zenimport-l6-backend-full.log.
- This is compatibility support for the extension's in-progress L6, not local-mode
  product readiness. Production services/data and real API accounts were untouched.
  No push, tags or publication.

# Local-mode L7 capture interoperability correction — 2026-10-21

- Reserved `legacy-v12` for verified legacy archive imports. Live discovery,
  incoming bank records and the direct record writer reject that marker before
  any write; valid history remains exportable. Existing verified legacy imports
  keep their separate path and portable format unchanged.
- Independent capture review found the defect while checking the browser port
  against Python. Both implementations receive the same regression guard.
- Full isolated Docker gate: 766 passed in 32.25s, including four new synthetic
  regressions; HTTP/auth and writer lifecycle checks also completed. Existing
  dependency/ZIP fixture warnings remain. Log: /tmp/zenimport-l7-backend-full.log.
- No production data/services, real API calls, push, tags or publication.


## 2026-10-21 — Preserve valid cross-job delivery authorization in portable archives

The standalone extension L9 source-parity test exposed an overstrict portable
history condition. A new synchronization click legitimately authorizes immutable
completed observations from an older job for the same physical account. This is
existing delivery.authorize_job SQL behavior, including history outside overlap.
Removed only the extra interval-job equality; complete revision/account/current
job snapshot and FK/insert proofs remain mandatory. Attempted identity/payload,
terminal history and unresolved-delivery protections remain unchanged.

Added an actual DeliveryHarness newer-click/older-capture archive regression.
Independent API review found no remaining issues. Full isolated Docker arm64:
767 tests passed in 38.05s (three existing warnings), HTTP/auth, SIGKILL recovery,
graceful stop and restored snapshot delivery lifecycle passed. Only disposable
synthetic data used. Log: /tmp/zenimport-l9-backend-history.log. No production
service/data/API, push, tags or publication.

### L10.1 — fresh portable migration identity (2026-10-21)

Added authenticated POST /api/v1/migration/portable/identity. It reads the current
SDK identity into a private temporary database under the exclusive writer gate;
it does not publish financial rows/cursors or clear holds. The existing migration
owner checks reuse the same projection validation. Errors are redacted.

The canonical portable HTTP contract is pinned to Chrome commit
65d3a3446dc1dff23ad533f0a6fa13148379aab3; its verified copy includes this additive
endpoint. Existing portable commands and ordinary API v1 retain their contracts.
Independent API review found no remaining issues. Chrome's real browser to this
isolated Python HTTP API identity/auth/permission check passed in 3.29s.

Docker: 32 focused portable API/service/contract cases passed in 11.98s; the full
isolated arm64 runner passed 769 tests in 32.78s (three existing warnings), then
HTTP/auth, SIGKILL recovery, graceful shutdown and restored journal checks. The
disposable Compose project/volumes/images were cleaned. Logs:
/tmp/zenimport-l10-identity*.log. No production data, push, tags or publication.

### L10.8 — bounded capture quiescence for shared-server transfer (2026-10-21)

Added authenticated POST /api/v1/migration/portable/quiesce with exact physical
installation/epoch CAS. Under the existing exclusive writer gate it cancels one
active capture, preserves received facts and never marks unfinished intervals
complete. Repetition reports readiness; final source_start independently rejects
new active capture. No SDK financial write or participant transition is performed.
The additive HTTP contract is pinned to Chrome commit
3b64daf4d06bde0fe47ce61ef474abe1210b94ca. Independent API/adversarial review is clean.

Full isolated arm64 Docker runner: 770 tests passed in 41.67s (three existing
warnings), HTTP/auth, SIGKILL recovery, graceful shutdown and restored journal
checks passed; disposable resources cleaned. Focused API/service/contracts:
33 passed in 13.03s. Actual installed Chrome production controller roundtrip and
foreign-client cancellation gates passed with zero financial writes, including
server capture stopping and native quota rollback. Logs:
/tmp/zenimport-l10-quiesce-backend-*.log and /tmp/zenimport-l10-controller-*.log.
No production service/data/API, push, Git tags or publication.

### Local mode L10 — separate physical and financial identity

Portable capabilities and authenticated fresh identity expose dataset_id separately
from installation_id. Import preserves physical installation identity while the
financial dataset follows the validated source archive. Chrome shared HTTP contract
is pinned to 4c30e917ce25a7be4c843ba9e3f54c6c76050ffd; no financial schema changes.

Isolated Docker: all 770 backend tests passed in 42.35s (three existing warnings),
plus HTTP/auth/SIGKILL/graceful-restart/restored-database gates. Chrome production
controller HTTP gate: 18 passed; minimum Chrome 140 lineage gate: five passed.
Independent API/adversarial reviews are clean. Synthetic data only, production
services untouched. No push, Git tags or publication.

### Local mode L10.14 — resumable format-2 server backups (2026-10-22)

Private physical schema 9 adds archive intent/chunk controls without changing
financial schema 1 or portable format 2. Upgrade retains physical identity and
journal, with a private schema-8 pre-upgrade backup. Format-1 schema-8 compatibility
remains available. New authenticated archive routes reserve a globally visible
snapshot hold, asynchronously export/graph-verify bounded chunks, then atomically
release only that own hold. No peer receipt or financial SDK request is created.
Explicit verified-download acknowledgement precedes one-chunk cleanup.

Independent API/adversarial reviews completed. Adversarial review found orphaned
scratch files after SIGKILL; fixed with installation-scoped cleanup under the
cross-process writer lock, including cancelled jobs, without following symlinks.
Final reviews report no findings. External cross-model CLI unavailable. Tests
include two actual SIGKILL cycles, process recovery, source preservation, foreign
path/symlink safety, exact replay, blocked retry, auth, epoch bounds and schema-8
upgrade retention. See docs/archive-backups.md.

Isolated Docker full runner: 778 passed in 40.89s (three existing warnings), plus
HTTP/auth, financial persistence, actual SIGKILL recovery, graceful shutdown and
restored delivery; disposable Compose resources cleaned. Latest focused backup
and added schema-8 upgrade gate: nine passed in 4.19s (one existing warning).
Logs: /tmp/zenimport-l10-server-backups-final-full.log and
/tmp/zenimport-l10-server-backups-complete.log.

L10 remains in progress: archive-only server recovery and major-gate review, then
L11 product UI and L12 acceptance. No live API/production data, push, tags or
publication. Local product routing stays gated in Chrome.

### Local mode L10.15 — explicit archive-only server recovery (2026-10-22)

New recovery routes reserve only an unused destination, with explicit retirement
of every former writer and acceptance of lost post-snapshot evidence. A fresh
full SDK read verifies the owner before upload reservation. Immutable bounded
chunks, full graph checks, fresh read-only reconciliation preview and explicit
acceptance precede atomic financial installation. Physical identity is retained;
financial lineage follows the archive. No peer/handoff receipt or SDK financial
mutation is fabricated. Imported delivery remains awaiting explicit First Sync.

Shared financial copying is private, behind separate live peer and archive-only
authority guards, or used solely in a disposable preview database. Cancellation
releases only this own unactivated hold. Lost activation replies replay only the
permanent activated outcome, including after cache cleanup. Cancelled/cleaned
attempts cannot impersonate successful activation. Explicit acknowledged cleanup
preserves permanent activation evidence and allows later backup after user sync.

Independent API/adversarial review found and fixed: terminal activation/cache
cleanup conflict, false successful replay after cancelled cleanup, preview freshness
before/after long graph/SDK/copy work, snapshot read time before projection/report
work, exact SDK raw-response hashing, tombstone-aware transaction counters, and
retryable classification of malformed fresh SDK projections. Final reviewers
report no remaining findings. External cross-model CLI unavailable.

Latest Docker complete suite: 795 passed in 139.70s (three existing warnings).
Latest focused backup/recovery/live-copy gate: 44 passed in 29.88s (one existing
warning). Full isolated Compose runner also passed HTTP/auth, financial persistence,
actual SIGKILL recovery, graceful shutdown and restored-delivery checks; disposable
resources cleaned. New tests kill recovery during graph validation and after
activation commit, prove retry without SDK financial sends, SQL-fault rollback,
late preview expiry before/after copy, stale summary construction, raw byte hash,
tombstoned reappearance and new backup after successful recovery/cache cleanup.
Logs: /tmp/zenimport-l10-server-recovery-final-all.log,
/tmp/zenimport-l10-server-recovery-final.log,
/tmp/zenimport-l10-server-recovery-full.log.

L10 remains in progress: installed Chrome archive-control interoperability and
major-gate review, followed by L11 popup integration and L12 complete acceptance.
No live API/production data, push, Git tags or publication.

### Local mode L10 major review — reserve complete participant fence paths

Independent major-gate adversarial review found that a source could acquire a
hold without enough int64 epoch values for cancellation/resume. source_start now
reserves four increments (start/seal/cancel/resume); destination_start reserves
three (start/arm/activate or discard), matching Chrome. Boundary rejection commits
nothing; exact last-safe successful/cancelled paths reach their terminal state
without overflow, counter reset or financial SDK mutation.

Independent reread reports no remaining budget findings. Docker focused
participant/live/archive gate: 85 passed in 35.13s (one existing warning). Full
isolated runner: 802 passed in 68.95s (three existing warnings), HTTP/auth,
persistence, SIGKILL, graceful shutdown and restored delivery all passed; temporary
Compose resources cleaned. Logs: /tmp/zenimport-l10-fence-budget-backend.log and
/tmp/zenimport-l10-fence-budget-backend-full.log.

Other Chrome major-gate corrections are still in progress; L10 is not complete.
No production/live data, push, Git tags or publication.

### Local mode L11 prerequisite — fence selected ordinary HTTP contexts

Ordinary API requests may now carry paired canonical X-ZenImport-Installation and
X-ZenImport-Dataset headers. The request gate verifies physical installation and
financial lineage before invoking the handler and remains held throughout it, so
an exclusive import cannot replace the dataset during the request. Legacy clients
without headers retain their existing behavior. Info advertises context_fencing=1;
portable/archive controls retain their own explicit CAS protocol.

Independent API review: no findings. Docker focused HTTP/bootstrap/live/legacy
migration suite: 74 passed in 20.94s (two existing third-party warnings). Log:
/tmp/zenimport-l11-context-fence.log. No live accounts, push, tags or publication.
