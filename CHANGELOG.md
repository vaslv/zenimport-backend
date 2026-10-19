# Changelog

## 0.2.1 — prepared, unpublished

- Accept BIDV VND payment-account history with isolated capture validation.
- Support named connections for multiple logins at the same bank; keep physical
  account mappings, coverage and delivery receipts shared across connections.
- Upgrade financial storage to schema 7 with a backup; accept schema-6 migration
  archives from 0.2.0. New schema-7 archives require 0.2.1 or newer.
- Recover legacy transfer-side ownership when newly captured bank history provides
  unique corroborating evidence, including cross-currency transfers. Ambiguous
  matches remain blocked; mobile edits/deletions are not overwritten or restored.
- Show unmatched bank and ZenMoney records on complete bank days, even when
  delivery fails. Match against current financial data, not stale UUIDs alone.
- Keep zenmoney-python pinned to public PyPI 0.7.0.

See [release preparation](docs/container-release-0.2.1.md) for verification and
upgrade boundaries. Earlier release: [0.2.0](docs/container-release-0.2.0.md).
