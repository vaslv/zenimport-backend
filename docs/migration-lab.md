# Server migration rehearsal: isolated environment

Explicitly invoked acceptance for a separate ZenMoney test user. Ordinary tests/CI
use only synthetic APIs. Tokens, user IDs, API responses, accounts, delivery journals,
and SQLite files stay in `.local/`, excluded from Git and standard Docker build context.

## Baseline

Before starting, save review fixes in separate local commits across all three projects.
SDK 0.6.1 is installed from a verified vendor wheel; Chrome 0.1.9; backend schema 6.
Do not update the production container/installed extension. Exclude neighboring SDK
`.idea/` from commits.

Pin test identity from the private journal of prior completed SDK acceptance
(`user`, `cleanup.state=verified`), never an arbitrarily entered new ID. Test tokens
must differ from production. Init reads production `.env` only to compare tokens
and prohibit the production HTTP port; containers neither mount that file nor receive
production tokens.

Existing account data may remain; never delete it to force emptiness. Acceptance
creates its own accounts and snapshots original entities for independent preservation
checks. Copying the entire production account is unnecessary at this stage.

## Creating the environment

From the backend root:

```sh
python3 scripts/migration_lab.py init \
  --root .local/migration \
  --token-file ../ZenMoneyAPI/.env \
  --identity-file ../ZenMoneyAPI/.local/live.json \
  --main-env .env
```

No network calls. Creates a run UUID, mode-700 directory, mode-600 `identity.json`
and `lab.env`, different A/B API tokens, and separate `server-a/`, `server-b/` SQLite
directories. No ZenMoney password is copied: the container reads `ZENMONEY_TEST_TOKEN`
from the supplied read-only file. Existing runs are never overwritten. Default
loopback ports are 18892/18893; override with `--ports PORT_A PORT_B`. Never use busy ports.

Set the printed path in a variable; do not print the file contents:

```sh
LAB_ENV=/absolute/path/from/init/lab.env
docker compose --env-file "$LAB_ENV" -f compose.migration-lab.yaml \
  --profile tools build tools
docker compose --env-file "$LAB_ENV" -f compose.migration-lab.yaml \
  --profile tools run --rm --no-deps tools inspect
```

Separate Compose does not inherit production compose.yaml. Project/image/network
names include the run UUID. Tools do not start A/B profiles. Containers are non-root,
read-only root, dropped capabilities, separate bind volumes, no automatic restart.
The ordinary backend image excludes live runners: use only explicit `migration-lab`.

Inspect performs a full read and verifies owner, token fingerprint, run UUID, and
required collections. It outputs only boolean checks/counts. The same verification
runs before each backend worker startup.

## Preparing the test catalog

```sh
docker compose --env-file "$LAB_ENV" -f compose.migration-lab.yaml \
  --profile tools run --rm --no-deps tools catalog --write
```

The user agreed this set in the acceptance plan: two new VND cash accounts, one USD
account, an expense category, an income category, and a test payee. Names contain
`ZenImport LAB` and the shortened run UUID. This command creates no transactions.
Do not use existing accounts as subsequent scenario targets.

Before the first write, fsync `catalog.json` with complete UUIDs/payloads, owner, run,
and original entities. flock excludes concurrent processes. Independent post-write
reads verify accounts/currencies/zero balances, categories, and preservation of original
entities. Repeats never resend catalog: they verify the saved intent only. Unknown
outcomes stop acceptance. Created entities are not automatically cleaned up.

## Starting A and B

```sh
docker compose --env-file "$LAB_ENV" -f compose.migration-lab.yaml \
  --profile a up -d --no-build --wait server-a
docker compose --env-file "$LAB_ENV" -f compose.migration-lab.yaml stop server-a
docker compose --env-file "$LAB_ENV" -f compose.migration-lab.yaml \
  --profile b up -d --no-build --wait server-b
docker compose --env-file "$LAB_ENV" -f compose.migration-lab.yaml stop server-b
```

Check `/health`, authorization, catalog, and empty bank/job/attempt tables first.
Mirror reads must create no financial requests. Never simultaneously run servers
with nonempty queues: flock protects one local database, not different servers.
Copy databases only after stopping A, through `backend.snapshot` with integrity/
ownership checks.

Stop the current lab project:

```sh
docker compose --env-file "$LAB_ENV" -f compose.migration-lab.yaml \
  --profile tools --profile a --profile b down
```

SQLite/journals remain in the private run directory. Do not run global Docker prune
or stop other projects. If Docker network ranges are exhausted, stop your own
environment first and run complete offline checks sequentially with the lab.

## Subsequent stages and criteria

1. **Controlled history.** Separate test bank page and independent oracle: expense, income, refund, four debt directions, both sides of ordinary/currency transfers, equal amounts, splits/merges, and new records beside existing ones. Stable bank IDs, exact Decimal movements, zoned timestamps; categories are not payment identity.
2. **Transferred database.** Import through A; repeat without creation requests; edit/delete only run-owned records; mirror; stop A; snapshot/restore B; switch popup in a separate Chrome profile; repeat the period; then new transactions only. Original UUIDs/mobile edits survive, deletions are final, every new record is created once.
3. **Empty database.** Independent test accounts. B first reads ZenMoney, recognizes every type/composite match by the bank side, and creates only positive count differences. Repeats create no UUIDs. Separately measure limits: deletion before observation, date/amount/account changes, unrelated equal-amount manual records, ambiguous groups, two physical accounts sharing a target. Never hide unrecoverable journal loss with another heuristic.
4. **Recovery and Chrome.** Lost post-write response, restart, tab closure, token replacement, unknown job/new tabId, unsafe server-switch rejection. Fake API first, then explicit live transport. Verify actual popup/separate profile, HTTPS, host permissions. Two localhost addresses do not prove external HTTPS acceptance.
5. **Independent reconciliation.** UUIDs, counts, actual creation requests, expenses/income/net per account/day, both currency sides, groups, deletions. Equal final balance alone is insufficient. Track initial balance separately; unverified results are not success.
6. **Publication.** Only after reporting: scan Git history for secrets/data, choose license, prepare CI, wheel/sdist, ZIP, image-based Compose, ARM64/AMD64, and explicitly invoked GHCR workflow. No production tokens in CI. Local commits only; pushing/tags/publication/production migration need separate permission.

## Lab code validation

`scripts/test-docker.sh` includes `tests/test_migration_lab.py`, never live requests.
Checks cover wrong/family users, other tokens/runs, incomplete snapshots, production
token/port, file permissions, bounded catalog, pre-request intent, lost responses
before/after application, read-only repeats, concurrent processes, and changed
original entities. Record live results separately below.

## Results

Initial reads on October 17, 2026 confirmed the previous separate test user: three
accounts, 18 categories, zero payees, two transactions. Token differs from production;
USD/VND available. Those two existing records were not deleted and do not make the
account production. Add further results only after actual validation.

On October 17, created only a new run catalog: three accounts (VND/VND/USD), two
categories, one payee. Independent reads confirmed fields/zero balances and preservation
of original accounts/categories/two transactions. A repeated catalog command verified
prior intent without another write.

A and B ran sequentially on 18892/18893 with separate tokens/database directories.
Both: health=ok, zenmoney_state=ready, three new catalog accounts, schema 6. Both
databases had zero bank accounts/transactions/jobs/deliveries/attempts/receipts/matching
targets. B additionally verified non-root UID/GID, read-only root, exact separate data
mount. Both stopped afterward; the lab network was removed and private data retained.
Production containers were neither stopped nor updated.

This completes environment/catalog preparation. Financial fixture import, snapshot
transfer, no-duplicate checks, actual popup/HTTPS were not yet performed or claimed.

Final preparation Docker run: **475 passed**, including 21 new lab checks.
HTTP/auth, restart, post-send SIGKILL, graceful shutdown, and snapshot recovery passed
without repeat UUIDs. Linux/ARM64. Live catalog checks above are separate from this
count. Private journals/checksums remain in the run directory, outside Git.

## Controlled history and sequential A/B replay

New runner: `scripts/migration_replay.py`. From the backend root:

```sh
python3 scripts/migration_replay.py --env-file "$LAB_ENV" --write
```

Rebuild the tools image first using the command above. The controller accepts only
generated `.local/migration/<UUID>` directories, separate Compose, and loopback ports.
One process holds flock. Docker/API responses/checkpoints stay private. Job
request_id/worker_id persist before HTTP; a lost ACK never creates another command.
Actual unknown outcomes never retry writes. Wait for running with temporarily
uncertain state after intent to resolve.

Two independent sets, `migrated` and `empty`, each with three VND/VND/USD accounts.
The first uses the prior catalog; the second creates its own three accounts. Add one
expense category for category-edit tests. Seed each with 18 ZenMoney records matching
19 bank records, plus two bank records absent from ZenMoney. Fixtures use the same
public HTTP contract as the extension collector. Native bank references are deliberately
absent: validate account/day/signed amount across all types/composite matches.

Contents: expense/income, purchase/refund, unknown category, two equal same-day amounts,
lend/collect/borrow/repay, both VND/VND and VND/USD transfer sides, one bank record
to two ZenMoney records (split), two bank records to one ZenMoney record (merge), and
mixed settlement. Two missing records must be created viewed=false. Later add one
VND expense and one USD income.

Transferred database: A initial (two new), A repeat (zero), then API-simulated user
category/comment edits and conversion of a new expense to debt; delete one owned
record. After refreshing the mirror, stop A and create a consistent B snapshot with
backend.snapshot. Old B history must create no records; edits/deletion survive.
Then two new records and a repeat without new requests.

Empty database: A imports the independent set (two new); after repeating, stop B,
archive its old database, and create an empty directory. Recreate account settings;
the new database must have a different instance_id. First/repeated B imports must
not recreate old transactions. Then two new records and another repeat. Preserve
database archives.

Freeze expectations before requests; fsync seed/catalog intent. Repeated seed steps
only read results, never resend UUIDs. A transport wrapper records actual outgoing
write IDs before HTTP, without tokens/descriptions/amounts/responses. An independent
verifier checks exact UUID sets/fields, categories/unreviewed state, daily expenses/
income, bank net per currency side, and balances. Separately verify virtual debt ledger
and unchanged original entities. Explicitly account for intentional deletion when
comparing bank data; prove nonrestoration by UUID and zero new requests.

This accepts real test ZenMoney and actual backend HTTP/SQLite. It does not test
Chrome popup, bank DOM, external HTTPS/host permissions, or mobile UI. API-simulated
user edits never substitute for mobile UI acceptance. The empty scenario deliberately
has no deletions before B first observes records: a lost journal cannot guarantee
recognition. Do not promise arbitrary database resets are safe. Record actual
outcomes after completing runs.

## Live migration acceptance — October 17, 2026

Both scenarios completed on the separate test account using SDK 0.6.1, backend schema
6, and actual HTTP API. Each set initially has 21 bank records, later 23 with two
new ones. Bank/ZenMoney count differences reflect both transfer sides and groups.

| Stage | New backend-sent UUIDs | Result |
| --- | ---: | --- |
| A, migration set: initial import | 2 | Remaining history recognized |
| A: repeat | 0 | Original UUIDs preserved |
| B, transferred database: old history | 0 | Edits/deletion preserved |
| B: repeat | 0 | No repeat write requests |
| B: two new transactions | 2 | Both confirmed |
| B: repeat including new records | 0 | No duplicates |
| A, independent empty-database set: initial import | 2 | Remaining history recognized |
| A: repeat | 0 | No repeat writes |
| B, empty database: old history | 0 | Existing records accepted without writes |
| B: repeat | 0 | No duplicates |
| B: two new transactions | 2 | Both confirmed |
| B: repeat including new records | 0 | No duplicates |

Twelve completed jobs; eight expected backend creations, no unexpected writes.
Thirty-six separate seed records were prepared across both sets. After intentionally
deleting one owned test record, 43 active trial records remain: 21 migrated, 22 empty.
Original account transactions are unchanged; no automatic cleanup.

Six checkpoints passed independent exact UUID/financial field checks, category/
viewed=false validation for new records, daily gross expenses/income, bank net,
each regular account balance, and debt ledger. In the migrated database, one 503 VND
record was intentionally deleted: bank comparison explicitly accounts for this user
choice, not an unnoticed mismatch. Two API edits (category/comment and expense → debt)
survived migration/subsequent syncs. Transferred instance_id matched A; the empty
database got another. Integrity/FKs passed.

The first run fixed a **test controller** bug: temporary uncertain after intent while
delivery_state=running was treated as final unknown. The backend had confirmed both
records; the controller resumed with the original request_id and no new delivery.
Added offline wait regression. Production financial backend logic was unchanged.

Private evidence: history.json, replay.json, stage2-results.json, saved SQLite/archives,
and transport audit under .local/migration/. Git contains no public API responses/
IDs/tokens. A/B stopped and their network was removed; production stayed unchanged.

Empty-start limitations remain: that independent set had no deletions or date/amount
changes before B first saw its bank records. Success does not reconstruct lost
history or prove arbitrary resets preserve user deletions. Prefer consistent transfer
of an existing database for such migrations.

Final offline Docker run: **489 passed**, including 35 lab checks (21 first-stage,
14 current). HTTP/auth, restart, SIGKILL, graceful shutdown, snapshot restore passed.
Linux/ARM64. Twelve live jobs are separate from the 489 tests. Private full-run logs/
SHA256 remain beside live results.

## Next stage: Chrome and HTTPS switching — October 17, 2026

Completed in neighboring ZenImportChrome using unchanged MV3 0.1.9, Chromium
145.0.7632.0, Docker Linux/ARM64. One extension profile per independent set switched
through ordinary popup fields from A HTTP to B HTTPS with a new API token. Bank
pages/API responses were synthetic; native permissions, TLS, backend/SQLite/SDK real.
Public endpoints, reverse proxy, toolbar popup, and mobile UI were not tested here.

Separate controlled history in the same historically verified test ZenMoney:
four owned VND accounts, two categories, 16 seed records. Twelve jobs completed.
A created two missing records per set; B created zero old records after switching,
then one new record per set. Every repeat had zero write IDs. Six expected UUIDs,
zero repeated/unexpected writes. Each set has 12 bank records corresponding to 11
ZenMoney UUIDs: a transfer covers both sides. Verified debt, repeated amounts,
categories, viewed=false, exact UUIDs/seed fields, daily movements, actual balances,
SQLite integrity/FKs. Existing test entities survived; no production data was used.

Permissions target specific hosts; no broad HTTPS grant. Chrome denial, invalid API
tokens, certificate hostname mismatch, and untrusted CA never replace old configuration.
CA trust is installed only into disposable container NSS; certificate validation is
never bypassed. A repeated independent Docker run without external networking also
completed 12 jobs. Chrome regressions: 31 unit + 17 browser/integration passed.
Production backend retained its container/port 18792; its code was unchanged.

Acceptance separately identified/fixed runner errors: waiting for popup button refresh
and completing the HTTPS case. Resumption used a verified completed prefix without
repeating seed/financial requests. Full report/commands/boundaries:
`ZenImportChrome/docs/server-migration-acceptance.md`. Local Chrome commits: 929fa32
and e8da2e2. Chrome private evidence:
`.local/browser-migration/c06cc618-abbd-4605-9640-6bef1d2e7f53/`.

This does not remove empty-database limitations: preserve earlier deletions/arbitrary
date/amount edits through database transfer and stopping the old worker, not journal
loss. No pushes, Git tags, or publication took place.
