# ZenImportBackend

Communicate in Russian. Plan: docs/refactor/README.md; status:
docs/refactor/05-progress.md; contract: docs/sync-api-v1.md.
This is an independent backend; legacy sources remain in Git at f3b2838.
Work in stages, run scripts/test-docker.sh, and update the progress log.
Create local Git commits for completed stages. Pushing, tags, and publication
require separate explicit user permission.
Do not use the production database or real ZenMoney in automated tests.
Install the SDK from the verified vendor wheel, not a neighboring checkout.
Do not add Chrome/connectors/editors/embedded bank UI to this project.
The user's October 16 request permits a read-only discrepancy report API;
its page belongs exclusively to the extension and performs no financial actions.
Check third-party library documentation through Context7 when changing its usage.
Distinguish actual live/platform validation from fixture-based checks.
