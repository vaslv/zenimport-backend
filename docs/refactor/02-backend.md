# Project 2: backend

Goal: an independent service receiving bank history and reliably adding new transactions to ZenMoney. Source: `/Users/vaslv/Projects/Vaslv/ZenImport/backend`. Proposed neighboring project: `ZenImportBackend`.

## B0. Fix boundaries and contracts

Inputs: `00-contracts.md`, current `backend/models.py`, `app.py`, `config.py`, SDK S1. Define HTTP schemas, versions, exact amounts/dates, request idempotency, size limits, authentication, and error codes. Prepare anonymized extension fixtures. Decide repository extraction preserving history without changing the source before validating copies.

Output: a concrete API contract, brief module/entity map, and test responses. Complete when E1 can use a mock backend without guessing formats. This is design work; never replace unknown bank facts with assumptions.

## B1. Extract an independent build

Preserve the working Python stack, pinned SDK, and reproducible dependencies. Transfer required runtime from the source. Proposed boundaries: HTTP API; settings/accounts; history ingestion; storage; ZenMoney mirror; learning; delivery queue; migrations. These are module responsibilities, not a requirement for a separate layer/class per function.

Remove SPA serving/browser editor dependencies. Do not remove old modules before identifying reused functions and migration sources. Classify `creation.py`, `zenstore.py`, `link_groups.py`, `reconciliation.py`, `category_drafts.py`, and `operations.py` as carried over / replaced / archive only.

Acceptance: clean build without SDK/extension checkouts, health, explicit database/token configuration, startup without ZenMoney writes. Document local/remote startup.

## B2. Storage and test-database migration

Inputs: `db.py`, `docs/zenmoney-storage.md`, `docs/sdk-migration.md`, `scripts/verify_migration.py`. Never assume the current schema number without reading actual code.

Entities: connections as access methods; canonical bank accounts/settings versions; source transactions and versions/aliases; jobs/intervals/pages; permanent delivery journal with UUIDs/attempts/deletions; ZenMoney mirror/cursor; confirmed learning evidence; legacy draft archive. Reuse current tables when appropriate; do not rewrite data merely for naming.

Database constraints, not code alone, enforce bank identity/UUID uniqueness. Preserve confirmed 1:N/N:M groups and exclusions. Version every schema change; repeating migration must not change results. Keep network calls outside long transactions.

Acceptance: synthetic legacy database migration, repeat startup, exact amounts, and preservation of every key/archive/link/exclusion/UUID. A real copy belongs to M1.

## B3. History ingestion, periods, and jobs

Persist pages before ACK; ensure repeat idempotency and account/job/settings-version ownership. Mark intervals complete only after every page/detail is checked, including valid empty results. Do not infer counts/coverage from the last transaction date.

Plan initial history, 14-day overlap, unfinished intervals, and one-time forced rereading. Repeated clicks join jobs; two clients must not capture/deliver one account concurrently. Disabling stops further work while preserving in-flight outcomes.

Acceptance: repeated/reordered/missing pages; changed account context; empty interval; month/year boundaries; long gaps; partial initial import; new account; disabling; version conflicts; rereading old complete intervals. One account's failure must not lose others.

## B4. ZenMoney mirror and prediction

Use the SDK; persist received data/cursor atomically, including deletions. Refresh the mirror and validate the target before sending. Bank capture may continue during ZenMoney outages; writes wait for freshness.

Learning uses proven links/current user categories. Repeated fetches, splits, and our unreviewed predictions do not increase example counts. Mobile changes/deletions update or exclude evidence. Eligible saved manual categories take precedence, then a rule/unambiguous history, otherwise empty. Never mix directions or arbitrary identical descriptions across different payees.

Acceptance: three equal examples; only two; another category; deletion; type change; repeated fetch; split; known payee without account; our viewed=false; manual choice. A new prediction never updates a previously sent transaction's category.

## B5. Durable delivery

Inputs: S1–S3 as needed, B2–B4. Persist UUID/prepared payload before networking. Eligible only for complete intervals/verified legacy data. Use SDK expense/income with explicit viewed=false. Skip previously processed/excluded records; send suspected semantic duplicates under the user's decision.

One queue executor: a short transaction records an attempt, then network, then result. A crash in attempting triggers UUID verification, never repeat upsert. For ambiguous/partial results, independently verify every UUID; apply returned diff to the mirror and verify remaining records with an appropriate SDK read. Mobile field changes on an existing UUID are never grounds to correct it. Never restore an explicitly deleted UUID.

Retry transient reads with bounded delay and server-response awareness. Prove write retry safety rather than infer it from one HTTP status. Indefinitely unknown outcomes remain a technical state with clear progress, without a financial editor.

Acceptance: fake API commits and loses response; deletes/changes before verification; partially accepts a batch; rejects before application; timeout; crash before/after request; closed Chrome; repeated button; archived target; service restart. No scenario generates a new UUID for an old transaction.

## B6. Docker and overall validation

One runtime image, non-root user, persistent SQLite volume, graceful stop, health. Local: loopback; remote: HTTPS/reverse proxy instructions. Do not add brokers/PostgreSQL/clusters merely because the project is split. Actually validate arm64 and amd64; honestly identify untested platforms.

Create independent `scripts/test-docker.sh` with a unique test Compose project/temporary volume. Cleanup only test resources. The original `scripts/test-docker.sh` is a reference, without unnecessary UI tests or production token/volume access.

Complete: unit/contract tests, HTTP checks, restart preservation, E5 integration, anonymized errors, recovery/sync-result documentation. Then M1, not immediate LIVE cutover.
