# Changelog

## 0.3.0 — unreleased

- Add portable format-2 archives and verified transfer/recovery APIs for the standalone Chrome client.
- Fence all financial delivery by database identity, context, generation and write authority; preserve ordinary API v1 for existing remote clients.
- Add resumable backup, chunk upload, preview/activation, exact-request cancellation and first-sync holds.
- Keep legacy SQLite format-1 recovery for conversion to portable archives. Never merge used databases or restore deleted financial operations.
- Keep zenmoney-python pinned to public PyPI 0.7.0.

Local candidate only; see [preparation and compatibility](docs/container-release-0.3.0.md).

## 0.2.1 — 2026-10-19

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
