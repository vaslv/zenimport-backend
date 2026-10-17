# B1: provenance and module map

Source project: ZenImport, commit `f3b283894872e0e0e732a817a2ada8928f73489a`.
New project: ZenImportBackend. Created with `git clone --no-local --no-hardlinks`; remote origin removed. The source repository and running container were unchanged during extraction. History is locally available through `git show f3b2838:backend/name.py`; financial data/`.env` were not cloned because they were untracked.

## Legacy runtime decisions

| Legacy module | Decision | Future responsibility |
|---|---|---|
| sync_contracts.py | Carried over | API v1 models; B1 adds only internal_error/method_not_allowed error codes |
| config.py | Replaced; create_env retained | Explicit absolute path, separate secrets, safe configuration generation |
| app.py / __main__.py | Replaced | Independent API/lifespan; only health/info in B1 |
| db.py | Replaced by storage.py bootstrap | Financial schema/migration in B2; old initialize never called automatically |
| creation.py | Git archive only; replaced in B5 | Persistent UUID, queue, independent result verification; no preview/manual reconciliation |
| zenstore.py | Archive only; replaced in B2/B4 | Exact projections, unknown fields, raw diff, cursor, and deletions; legacy schema sources preserved in docs/zenmoney-storage.md |
| link_groups.py | Archive only; data retained in B2 | Confirmed 1:N/N:M links remain history; matching UI does not return |
| reconciliation.py | Archive only; useful logic transferred after B4 review | Payee identification and confirmed examples without blocking ambiguous new records |
| category_drafts.py / category_send.py | Archive only | Legacy edits to existing ZenMoney records are archived; no new delivery of local edits |
| operations.py | Archive only | Type/debt/transfer editors removed; users work in mobile ZenMoney |
| service.py / day_review.py / splits.py / batch_send.py / exclusions.py | Archive only; responsibilities redefined in B2–B5 | Capture, permanent exclusions, and delivery without editors/reconciliation |
| zenmoney.py / jsoncodec.py | Archive only; SDK used directly | Lifecycle client already in app.py; exact B4 serialization comes from the SDK |

Archive only means the module is absent from the working tree/runtime; its code remains in Git. Imports/functions and preserved table purposes were reviewed before removal. B1 does not claim financial logic migration: tables, archives, exclusions, UUIDs, and links are validated in B2/M1. Do not copy the old sender wholesale: it applies user edits and contains legacy candidate checks.

## New boundaries

Initially: HTTP/config/storage bootstrap/models. B2 adds financial storage and explicit migrations; B3 adds settings, page ingestion, and planning; B4 adds mirroring and learning; B5 adds durable delivery. These are modules in one service, without extra microservices, a broker, or a database engine change.

Docker no longer contains browser/parser targets, HTML demo, or extension dependencies. The build context allows only required backend/dependency-lock/test/config files. The public PyPI SDK wheel is SHA256-verified during installation; SDK source and a vendored wheel are not required.

B1 assigned the new database application_id `0x5A494231`, schema 1, and service_meta/instance_id. B2 added financial schema **2**, private backups, and transactional upgrades of own v1. Legacy/foreign/future schemas are still rejected before changes. Legacy v12 is explicitly converted into a new file: [financial-storage.md](financial-storage.md). The production database was not migrated.

B3 added [history ingestion and settings](capture.md): accounts/capture/capture_policy/sync_api, schema **3**, and own v2 upgrade. Jobs/snapshots/pages now work over HTTP; legacy editors/reconciliation did not return. B4 added mirror/predictions and schema **4**; the SDK reads current data and prediction uses independent confirmed transactions. See [mirror-predictions.md](mirror-predictions.md). B5 added delivery.py, schema **5**, an independent worker, durable intent, and UUID verification without overwriting mobile edits: [delivery.md](delivery.md).

B6 added snapshot.py and independent validation of both architectures/container recovery: [b6-validation.md](b6-validation.md). Operations: [operations.md](operations.md); E5 requires the new extension.

## B0 → B1 contract

Implemented GET /health and GET /api/v1/info, shared Bearer/auth, body limits, and error formats. B3 routes and the B5 sender are implemented. Swagger/HTML and legacy mutating endpoints are not served. A configured token does not prove freshness: B4 exposes ready only after a successful SDK refresh no older than 300 seconds. Invalid configuration prevents startup rather than creating a demo database.
