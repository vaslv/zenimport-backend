# Project 1: ZenMoneyAPI

Goal: use the already extracted general-purpose library rather than rewrite it for the new backend. Repository: `/Users/vaslv/Projects/Vaslv/ZenMoneyAPI`.

Read local AGENTS.md, `docs/coverage.md`, `docs/live-results.md`, then only the affected `src/zenmoney_api` module. The user does not require backward compatibility with the old backend; nevertheless, the new refactor does not inherently require breaking the public SDK.

## S1. Audit required capabilities

Check `client.py`, `transactions.py`, `protocol.py`, `errors.py`, and existing tests:

- Initial/incremental diff, forceFetch, and explicit deletions.
- Expense/income with a supplied UUID, exact amount, valid accounts/instruments, and a category or no category.
- Explicit `viewed=false` through builders to the actual HTTP body. The general default is currently `viewed=True`; the backend must pass false rather than change the library default globally for one application.
- Find results by UUID in received diff; do not invent an undocumented GET transaction.
- Distinguish pre-send failures, HTTP errors, malformed responses, and timeouts as far as transport/protocol allow. The SDK performs no hidden write retries.
- Preserve unknown response fields and exact money. Do not reduce the API to expenses/income: existing transfers/debts remain general SDK features.

Output: a short requirement → existing method → test → gap matrix. If everything is covered, finish without SDK changes or a new version.

## S2. Fix only demonstrated gaps

Depends on S1. For each gap, first add an observable behavior test, then the minimum public API/error change. Do not add databases, bank names, payee categories, application tombstones, or queues. Update examples/typing and document backend changes when modifying the API.

Acceptance: `scripts/test.sh` in Docker, installed package, strict typing; sync/async coverage for the affected contract. No automatic live requests. Existing expense, income, transfer, and debt tests must still pass.

## S3. Hand an artifact to the backend

For actual changes, build with `scripts/build.sh` and record version/checksum. The backend installs a verified wheel, not neighboring SDK source. Verify imports and the contract test in a clean backend container. Without changes, retain the verified current dependency.

Complete when B5 can construct and verify a new unreviewed transaction through the SDK without manually assembling ZenMoney JSON in business logic. Package publication, pushing, and tags are outside this stage.
