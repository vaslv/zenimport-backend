# ZenImport: bank history capture without a transaction editor

Created through office-hours: October 15, 2026, Asia/Ho_Chi_Minh.
Status: IMPLEMENTATION AUTHORIZED (October 15, 2026)
Mode: Builder
Source project: /Users/vaslv/Projects/Vaslv/ZenImport, main, 9271b3b.
Library: /Users/vaslv/Projects/Vaslv/ZenMoneyAPI, package version 0.6.0 in use.
This is a design decision. Implementation started with B0/S1; stage status lives in
the progress log. These stages did not migrate production databases or send data.

## User problem and outcome

The user signs into the bank independently, opens the extension, and clicks
Synchronize. The extension captures bank transactions; the backend adds missing ones
to ZenMoney. Every new record is unreviewed. Users handle categories, split purchases,
transfers, debts, and deletions in the official mobile app.

Chrome keeps only the popup: synchronization, brief progress, expandable settings.
Bank pages receive no UI elements. Remove the separate SPA, reconciliation pages,
transaction editors, send-confirmation modals, and embedded-interface toggle.

The user rejected delaying ambiguous matches: delivering every new bank transaction
is more important; possible semantic duplicates will be deleted in ZenMoney. This
can deliberately alter balances/reports temporarily until phone review.

## Agreed decisions

- D1, A: ZenMoney edits/deletions are final. Never overwrite edits or restore deleted transactions when rereading the bank.
- D2, B: three independent projects: SDK, backend, Chrome extension. Bank connectors remain separate extension modules.
- D3, B: possible ZenMoney matches do not delay new transactions. No manual browser reconciliation.
- D4, user clarification: first connection shows all discovered bank accounts; map each to an existing ZenMoney account or Do not import. Default initial start is 30 days ago, with six months/custom dates. Subsequent capture starts from the previous successful boundary with a two-week overlap.
- Users select a start date; capture ends on the bank's current date frozen at job creation.
- Previously agreed category threshold: at least three confirmed examples for one payee/category and none for another category; uncertainty leaves it empty.

## Project boundaries

Working directory names: ZenImportBackend and ZenImportChrome alongside ZenMoneyAPI.
Final renaming of the original repository belongs to engineering planning; history
must remain accessible.

| Project | Includes | Excludes |
|---|---|---|
| ZenMoneyAPI | HTTP, API models, exact amounts, object builders, errors | Application database, bank parsers, learning, sync queue |
| Backend | Bank history, ZenMoney mirror, account settings, categories, durable queue, write verification | Bank sign-in automation, HTML editor |
| Chrome | Popup, connection settings, discovery, capture, page upload, progress | Financial editor, ZenMoney token, delivery decision policy |
| Bank modules | Site/context detection, accounts, period limits, pagination, details, normalization | ZenMoney delivery, categorization, shared popup UI |

Server-controlled delivery is selected. Merely separating directories was rejected:
it would retain browser-dependent delivery. A fourth connector project is deferred
because connectors have one consumer so far.

Use a pinned verified SDK package. Backend builds must not require a neighboring SDK
working directory. The extension has its own build/tests and uses versioned backend HTTP.

## Popup and first connection

Sketch: [zenimport-popup-wireframe.html](zenimport-popup-wireframe.html). It shows
three states, not an additional product page.

### Ordinary state

Extension name, detected bank, Synchronize, one short result line, collapsed Settings.
The button applies to the active tab's bank connection and all enabled accounts.
Other bank tabs are not processed automatically.

Clicking starts capture/delivery without another transaction preview. Closing the
popup does not cancel the job. The same primary area shows stage/account/transaction
counts while running. If cancellation is needed, temporarily replace the primary
action during capture; delivered records are never rolled back.

### First connection

1. Without a connection, expand Server address and Connection token automatically. This is the backend token; configure the ZenMoney token on the server.
2. Check server/API compatibility and load eligible ZenMoney accounts. If the bank is closed/login expired, show a brief sign-in instruction.
3. Detect the bank, accessible profile, and all supported session accounts. Never collect login/password/cookies/OTP for backend transmission.
4. Each account shows title, currency, masked suffix, and selection: Select a ZenMoney account, compatible targets, Do not import. Empty selection is not consent to skip. Initial synchronization waits while rows remain unselected.
5. First release targets active regular same-currency ZenMoney accounts; archived/deleted/debt accounts are unavailable. Users create missing accounts on mobile and refresh the popup list. Extension account creation is outside scope.
6. Initial period is shared by currently selected accounts: 30 days ago, six-calendar-month shortcut, custom date. Future dates are prohibited. Report bank history limits explicitly; never silently shorten the period.
7. Synchronize saves settings and starts. If all accounts are Do not import, show No accounts selected and send nothing.

### Connection changes

- Expandable popup settings contain server/token, mappings, and period. Save without logging tokens.
- Discovered accounts are unconfigured, enabled with a ZenMoney target, or disabled. Disabled accounts are neither read nor deliver their old unsent records.
- Disabling affects active jobs: before the next bank request/send, backend/extension recheck authorization. Requests already sent to the bank/ZenMoney cannot be retroactively cancelled; retain/verify results without rollback. Pause the remaining queue with original UUIDs. Reenabling alone never sends; require another Synchronize click.
- Show newly discovered accounts in settings. Configured accounts can continue; never silently enable new ones. Enabling selects a start date as at first connection.
- Absence from one bank response never deletes settings or proves account closure.
- Target changes apply to future unsent transactions. Existing links/deletions/sent records retain old IDs. Active/uncertain delivery freezes mapping changes until verified.
- Separate bank profiles are separate connections. If the connector cannot reliably distinguish them, require popup connection selection; never guess from owner names. A provably identical physical account across connections shares identity/delivery history.
- ZenMoney mapping, enabled state, start date, and coverage belong to the shared bank account, not access method. A second connection shows existing settings; competing mappings are prohibited. Version settings so stale saves cannot overwrite newer ones. Serialize account capture across connections. Never merge profiles without bank identity evidence.
- Before preparing every delivery, check the target against a fresh mirror. Deleted/archived/incompatible targets retain queues and show a brief reason; other accounts continue. Existing settings permit mapping correction while preserving started-delivery restrictions.

## Periods and completeness evidence

For each bank account, retain import start, continuous completed-interval coverage,
last complete boundary, and unfinished intervals. Button time, newest transaction,
and ZenMoney delivery results never substitute for complete bank capture evidence.

- First run: selected date through current bank date.
- Next complete run: max(import start, last complete end minus 14 calendar days) through current bank date.
- After two months without syncing, capture the whole gap plus two weeks, not merely the last 30 days.
- Incomplete initial runs resume unfinished selected-history intervals. Do not shorten the window while gaps remain. Reuse completed intervals, but newer successful islands cannot jump gaps.
- A successful empty response with verified account/period/pagination completes an interval too.
- Each connector defines request limits/time zone. VietinBank retains windows up to 30 days. Test boundaries, month/year changes, leap dates.
- Collapsed settings contain an initially empty One-time history reread from field. Saving a nonempty date schedules the range through current date for the next Synchronize regardless of coverage/ordinary formula. This finds late records in completed periods and can extend import start earlier. Clear only after full capture; failures retain unfinished ranges. Later ordinary runs return to 14-day overlap. Saving server/token with an empty field never schedules rereading.
- Moving the start forward never deletes bank records/links/delivery history. Synchronization processes all accumulated undelivered records from enabled accounts, even outside the new capture window. Explain this beside history settings.
- Late bank records older than two-week overlap are not found automatically. Old-period rereading is the recovery path; never promise completeness beyond verified coverage.

## Capture, persistence, delivery

1. Popup initiates a specific-connection job, freezing enabled accounts, mapping versions, and period end. Current disabling overrides the job snapshot. Backend returns a stable job ID. Repeated clicks join the active job rather than send in parallel.
2. Refresh ZenMoney IDs, deletions, accounts, categories, and user edits for learning. If unavailable, bank capture/storage can continue, but delivery cannot.
3. Extension sequentially reads enabled accounts through connectors. Server persists accepted pages before browser ACK. Popup/worker restart never loses acknowledged data.
4. A separate interval completion message verifies all pages, counts, bounds, and account. Only complete intervals make their new records eligible. Partial data is retained for resume, never called complete history.
5. Backend prepares completed intervals and eligible old unsent records for enabled accounts; skip exclusions/prior delivery. Ready intervals may send while the browser reads later ones. Another account's error never invalidates confirmed results.
6. Assign categories only from sufficient confirmed history, otherwise empty. Bank movement sign determines expense/income. Users turn new records into transfers/debts/refunds/split purchases in ZenMoney.
7. Persist the permanent ZenMoney UUID/send intent before API access. Send bounded batches and verify through separate SDK reads.
8. After capture, browser availability is unnecessary for delivery. Backend resumes queues after Chrome closure/its own restart. If Chrome closes during capture, unread intervals wait for the next sign-in/click.

The server implements a durable database queue. First release uses one instance/
executor without a message broker. Keep network waits outside long SQL transactions.
Short transactions atomically persist queue state; serialization/recovery are tested.

No periodic hidden imports or new sends merely from bank page opening. Synchronize
authorizes delivery; executing that job can continue on the server afterward.

## New records, possible duplicates, deletion

Bank identity combines bank, stable bank account, and external transaction ID.
Browser connections are access methods, not reasons to duplicate accounts.

- Historical links/confirmed delivery mean already processed. Never recompute this from current amounts/categories after mobile edits.
- For unknown transactions, exact proven bank IDs on the corresponding ZenMoney side/account may establish existence. Equal date/amount/description/combinations remain assumptions and do not block delivery. No manual matching UI.
- Send every new record viewed=false, even confidently categorized ones. Never automatically replace an empty category with Adjustment.
- Known ZenMoney UUID deletion permanently prohibits recreating the corresponding bank record. Delivery/deletion facts survive mirror cleanup.
- Partial-response absence is not deletion. Separately retain explicit deletion and temporarily absent delivered records; neither authorizes recreation.
- Another bank transaction with equal amount/date/payee does not inherit a deletion prohibition.
- Phone splitting/transfer conversion must not resend the original bank record. Preserve 1:N/N:M groups. If the API cannot prove new child-ID links, do not invent them or use the record for learning.
- Bank edits to known transaction fields create a new source version, never corrections to delivered ZenMoney records.

### Lost API response is not a semantic duplicate

Read by persisted UUID after response loss. UUID presence or explicit deletion proves
it cannot be recreated. Different fields may reflect mobile corrections; never
restore old payload values to make verification pass.

Unknown outcomes prohibit new UUIDs/blind upserts. Synchronize resumes verification.
If the API cannot establish outcome, retain a technical error with brief status/
diagnostics; this does not restore financial browser reconciliation. Automatic
delivery during indefinite API outage is not guaranteed.

Known pre-send/proven rejected requests may be corrected/retried with the original
ID. Fix error classification/retry evidence through contract tests; no arbitrary
HTTP code proves that nothing was written.

### Identity limits without bank IDs

Current VietinBank parsing uses account/time/amount/balance/description fingerprints
when trxId is absent. Field changes can change fingerprints. Preserve old keys/aliases
during migration; parser updates must not recreate history. Never restore deleted
applies to recognized identity; mutable data without stable IDs prevents absolute
guarantees. This is a connector risk requiring samples/tests, not reconciliation UI.

Another limit is a record created elsewhere and deleted before first observation.
An unknown UUID without a bank link cannot prove which bank row must be suppressed.
Protect known links/deletions, but do not invent missing history. This follows the
chosen rule of sending ambiguous new records.

## Categories and learning after mobile review

- Store immutable bank sources separately from current ZenMoney records. Read user categories/types from current ZenMoney, not stale sent objects.
- Use confirmed links/payee rules with decision provenance. Prefer counterparty account, then verified normalized name/rule, then description if details are absent. Never merge arbitrary descriptions or fees with main payments.
- Automatic assignment needs three different reliable bank payments, one category, matching direction, and no conflicting examples. Split payments/transfers/debts/unreliable links do not establish one category.
- Our new viewed=false predictions never train. New imported records become examples only after ZenMoney viewed=true and an eligible expense/income category. Successful sending is not user confirmation.
- Historical manual confirmations retain their trust source. Repeated reads/multiple pieces of one payment never increase independent counts.
- User category/type edits/deletions change or remove evidence. Never assign inactive/deleted categories.
- Recalculate before preparing unsent records. Never update sent records because predictions changed.
- Saved manual categories outrank new predictions if still valid for the resulting expense/income. Archive invalid original choices and apply the general confident-prediction-or-empty policy before sending. Never mechanically transfer debt/transfer categories to ordinary records.

## Connector contract and trust boundaries

Common interface: determine page support; obtain connection/account context; describe
time zone/currencies/depth/window; read account/period history; fetch extra details;
return validated records, progress, and completeness evidence.

Records contain stable external ID or explicitly versioned fingerprint, bank account,
known-zone timestamp, direction, exact amount/currency, description, counterparty
details, and all available transaction fields. Separate normalized common fields from
bank-specific originals. Original responses must exclude cookies/tokens/authentication.

Where details require another request, completeness includes them. Explicitly mark
unavailable optional fields; never silently claim all details. Invalid amount/currency/
account never becomes a guessed transaction.

No global activity listeners for infinite session keepalive. Use necessary requests
during active capture; pause and request sign-in on expiry. Never simulate bank
payments or settings changes.

## Migrating current data

Proposed safe migration without resetting existing storage:

1. Stop legacy delivery/active extension jobs. Save a complete verifiable SQLite/WAL backup and application/schema versions. Retain original database/old image for rollback.
2. Convert a separate copy in Docker without production ZenMoney. Preserve all bank IDs, raw data/archives, accounts/mappings, link groups, exclusions, manual categories/rules, current mirror/cursor, journals/intents with original UUIDs.
3. Move confirmed links into permanent processing history regardless of current mirror target presence. Retain explicit deletions as separate events. Legacy bank exclusions stay excluded; disabling an entire account is separate configuration.
4. Archive local edits to existing ZenMoney records without applying them over mobile decisions. Preserve eligible manually chosen unsent categories as hints; archive local debt/transfer types for subsequent phone review.
5. Proven unstarted jobs retain UUIDs, rebuild as expense/income, viewed=false. Started/uncertain attempts retain exact payload/ID and are verified first. Old viewed=true alone never triggers resend.
6. Transfer coverage only with full account/range/page evidence. Otherwise mark unknown and reread the selected initial range with deduplication. Never infer successful capture from the newest transaction.
7. Compare source/target counts/keys, complete raw records, group participants, exclusions, UUID/attempt history, exact amounts. Derived tables must not lose source records. Check integrity/idempotent reruns.
8. Never run old/new backends simultaneously on one database/token. Explicitly select the new volume; avoid accidental emptiness from Compose project renaming.
9. First startup reads/verifies unfinished delivery. New records require Synchronize, not migration alone.

After actual new writes, rollback cannot simply restore an old snapshot that forgets
new UUIDs. Preserve the current delivery journal during version rollback or use a
separately agreed recovery/ID reconciliation process. Test before cutover.

## Docker, data, distribution

- First release is a personal backend for one ZenMoney profile, with multiple bank connections/accounts. Multiuser service is outside this refactor.
- One runtime image locally/remotely, persistent database volume, runtime configuration, no local absolute paths/secrets in images. Retain SQLite; remote hosting alone does not require PostgreSQL.
- Local ports bind loopback. Remote instructions include HTTPS reverse proxy, persistent storage, backups. Extension/backend token is separate from backend-only ZenMoney token.
- Require actual linux/arm64 and linux/amd64 builds/tests, non-root execution, health, graceful stop, queue recovery. Never claim untested platform support.
- Each project owns dependency versions/tests/instructions. Verify extension/API compatibility before work and explain mismatches in popup.
- Initially distribute a reproducible unpacked-extension ZIP and reproducible local backend/container build. CI builds/tests may be prepared; image publication, Chrome Web Store, Git push/tags require separate permission and are not performed now.
- Never clean source databases/archives during migration. Bound new diagnostic messages by size/retention and exclude secrets. Financial archive retention is separate discussion, not deletion during this refactor.

## Verifiable acceptance criteria

1. Clean installation: two mapped bank accounts, a third disabled and neither read nor delivered. Unconfigured never silently enables.
2. Initial period defaults to 30 days; six months/custom date work; read all pages/available details.
3. Subsequent capture starts from complete boundary minus 14 days. Test long gaps, empty periods, incomplete first runs, separate-account failures. One-time rereading finds late records in completed ranges and returns to ordinary windows afterward. Second connections reuse settings; disabling stops new attempts without losing in-flight results.
4. One uncategorized, one confidently categorized, and one ambiguously matching new record all arrive viewed=false.
5. Repeats create no technical duplicates. Deleted ZenMoney records never return; a different same-amount purchase is created.
6. Phone category/amount/account/type edits and splits are never overwritten and correctly change/exclude learning examples.
7. Lost/partial responses, backend/Chrome restarts, repeated clicks, and two browser connections never create new UUIDs for one attempt.
8. Popup closure loses no job; after capture, delivery finishes with Chrome closed. Interrupted capture never claims complete success.
9. No bank DOM elements/panels/statuses. No separate product pages/transaction editors in the extension.
10. Copy-based migration preserves data/keys/links/exclusions and repeats unchanged. Test rollback/current delivery journal preservation after sending.
11. Docker checks use neither production database nor real tokens. Validate both target architectures/persistent volumes.
12. Actual user-app check: viewed=false appears in the expected new/unreviewed list; deletion then synchronization never restores it. Check iOS separately: prior official Android explanation does not prove current iOS behavior.

## Next steps

1. Direction accepted by the October 15 implementation request. Prepare proposed legacy draft migration on a copy first.
2. Engineering planning: final data/API contracts, migration schema, delivery/deletion journal, recovery, formal identities, tests.
3. Establish missing VietinBank facts: profile account lists, all details, stable IDs, actual depth. Validate before related contracts; never assume bank capabilities.
4. Extract/test queued backend, then popup-only extension/VietinBank module. Remove legacy UI only after new end-to-end coverage.
5. Copy migration rehearsal/mobile new-record checks, then separate production cutover preserving originals/delivery history.

User action before LIVE acceptance: select one future genuinely new transaction,
check its unreviewed phone display, delete, and synchronize again. Never destructively
test a historical transaction that must be retained.

## Open technical questions

- Complete account discovery/stable profile identifiers require actual VietinBank responses; current collector handles one account.
- Identity stability without trxId: changed descriptions/balances must not silently bypass resend prevention. Need fixtures/explicit limits where identity cannot be proven.
- viewed=false/review confirmation in current iOS remains untested.
- Exact HTTP schemas/final repository names belong to engineering planning; agreed user rules do not depend on them.

## Decision drivers

- No browser reconciliation excludes bringing back editors disguised as diagnostics.
- Treat all ZenMoney changes/deletions as final makes mobile the source of user decisions.
- Multiple accounts in one bank profile require per-account configuration/progress, not one global last-sync date.

## Design review

Independent review: two passes, six findings resolved, final PASS, reviewer 9/10.
Clarified one-time rereading, disabling active queues, shared settings across connections,
target validation, limits of deletion evidence, and manual-category priority. No
remaining review findings; listed technical questions remain preimplementation checks.

HTML sketch saved. Automated visual validation was not completed: the browser helper
could not open the local port in this environment. This is an interface sketch,
not a tested extension implementation.

On October 15, 2026, the user requested sequential implementation with local commits.
This design is accepted as the basis. Decisions above describe future implementation,
not a report of application changes.
