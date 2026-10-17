# Shared rules and contract boundaries

## Fixed requirements

1. All ZenMoney changes/deletions are final. Never update or recreate already delivered bank transactions.
2. Preserve bank source, persistent identity, destination UUID, delivery attempts, and known deletions independently of the current mirror.
3. Before creation, preserve prior confirmed links and search existing ZenMoney records: bank reference first, then target account, calendar day, direction, currency, and exact amount. Match equal amounts chronologically, using each UUID side on a particular account once. One transfer can represent an expense on one account and income on another. Create only missing multiplicity; extra ZenMoney records remain in the read-only report. Bank references take priority, but another reference in a description does not invalidate a day/amount match. Transaction type never restricts the search: use signed movement on the selected account. Preserve unambiguous splits, combined fees, and settlements as groups. Never edit matched records. This October 16 user clarification supersedes unconditional creation on semantic similarity.
4. Unknown HTTP write outcomes are different: verify the previous UUID first; never use a new UUID or blind retry that could restore deletions or overwrite edits.
5. New records use bank-direction expense/income, `viewed=false`, and a category only from a saved manual choice or confident prediction. No automatic Adjustment.
6. Prediction requires three independent reliable payments to the payee in one category, without conflicts, in a compatible direction. Our unreviewed predictions are not training examples.
7. Popup is the only product UI: synchronization, progress, expandable settings. No bank UI injection, toggle, SPA, editor, delivery preview, or reconciliation.
8. Bank/account setup maps each account to an existing compatible ZenMoney account or Do not import. Unconfigured is not disabled.
9. Initial capture: 30 days ago / six calendar months / custom date. Subsequently per account: `max(import_start, last_complete_end − 14 days)` through the frozen job end in the bank time zone.
10. Never skip incomplete initial-import gaps. A verified empty period is complete. One-time manual rereading bypasses old coverage.
11. Bank passwords, cookies, and OTPs never reach the backend; ZenMoney tokens stay backend-only. Automated tests use fake data/tokens.
12. One backend instance, persistent-volume SQLite, durable queue. Delivery continues after Chrome closes.

## Ownership

SDK: ZenMoney protocol, serialization, exact amounts, error types. Backend: settings, identity, history, coverage, prediction, job authorization, delivery. Extension: bank session, discovery, page/detail capture, progress. Bank modules know no ZenMoney delivery policy.

A proven identical physical bank account shares mappings, import authorization, and coverage across connections. Disabling takes precedence over saved jobs. Started requests are only verified; unstarted ones stop. Remapping never changes delivered UUIDs and is blocked for uncertain/active delivery.

## Contract B0 must fix before integration

These are responsibilities, not claims about existing endpoints. B0 defines concrete schemas and anonymized versioned requests/responses. Later stages use one approved contract rather than invent variants.

| Action | Required data / result |
|---|---|
| Connection check | API version, client compatibility, secret-free ZenMoney status |
| Discovered account registration | Bank, access context, identity evidence, currency, title; absence in partial discovery never deletes an account |
| Settings | Account ID, expected version, enabled/ignored/unconfigured, target ZenMoney ID, start date; conflicts never overwrite newer settings |
| Job creation | Connection, idempotency key, optional one-time period; return ID, interval plan, settings versions |
| Page upload | Job/account/interval IDs, stable page ID, schema version, normalized records, allowed raw fields; ACK only after commit |
| Interval completion | Bounds, pagination/count, consistency evidence; server validates even zero results |
| Progress / stop | Separate capture, delivery, and per-account errors; stopping never undoes sent records |
| ZenMoney accounts/categories | Only information needed for mappings; revalidate targets before sending |

Normalized transaction: bank/account identity, external ID or versioned fingerprint, zoned date/time, exact amount/currency, direction, description, counterparty, available details, parser version. Amounts are decimal strings with defined scale, without implicit float conversions. Fix amount/sign representation once in B0; both sides validate consistency.

Machine-distinguishable errors: bank login expired, server unavailable, incompatible version, changed settings, incomplete history, unavailable ZenMoney target, transient read error, unknown write outcome. Never collapse these into synchronization successful or one generic error.

## Minimal state model

- Interval capture: planned → collecting → complete; errors/login/stop retain unfinished intervals. Repeated pages do not increase transaction counts.
- Delivery: pending → prepared (UUID persisted) → attempting → delivered or uncertain. Restart in attempting means uncertain. Explicit known UUID deletion is terminal suppressed; delivered never returns to pending.
- Safe retries require proof the write was not applied; SDK contracts validate specific error policies. UUID absence from a partial diff is not proof.
- Successful complete bank capture is independent of successful ZenMoney delivery. These are separate checkpoints.

## Unestablished facts

Actual responses must verify complete VietinBank account listing, profile identity, details, and history depth. Some rows lack stable IDs; old fingerprints contain mutable fields. Preserve legacy aliases and do not promise impossible deduplication for unknown identities. A transaction deleted before first observation cannot be recognized from an unknown UUID alone.

`viewed=false` is sent explicitly, but display among new transactions in the current iOS client requires separate verification. This does not justify changing payloads to `true` or claiming mobile acceptance from unit tests.

User clarification, October 16, 2026: alongside the popup, permit one separate read-only Discrepancies extension page showing potentially extra ZenMoney transactions for selected banks/accounts and completely captured days. Corrections/deletions happen in mobile ZenMoney; bank inline UI, SPA, and editors do not return. This supersedes item 7's prior prohibition of any reconciliation/separate page.
