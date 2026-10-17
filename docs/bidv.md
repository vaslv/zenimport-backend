# BIDV capture policy

Status: **0.2.1, local/unreleased**. Pair with ZenImport Chrome 0.2.1+. The BIDV policy itself needs no schema or SDK change. The same development
version also adds [named connections and schema 7](bank-connections.md). The backend still performs no bank HTTP requests.

The v1 discovery allowlist now accepts `bank=bidv` as well as `vietinbank`. BIDV
requires Asia/Ho_Chi_Minh, included details, VND with scale zero, and capture windows
of at most 30 days. The Chrome connector reports the bank's rolling one-year lower
boundary. Existing account settings and period validation enforce that boundary.

The bank-specific detail allowlist is `transactionDate`, `referenceNumber`,
`transactionRemark`, `transactionAmount`, and `balance`. VietinBank's existing
allowlist is unchanged; neither bank can submit the other's fields or authentication
metadata. Account identity includes bank, so an equal account number at another
bank does not share mappings or delivery receipts.

No change to matching/delivery: signed day/amount matching spans all ZenMoney types,
new records are unread, repeated capture reuses durable identities, and mobile edits
or deletions are final. A BIDV reference alone is not a unique posting identifier;
the connector sends its composite fingerprint with exact posting amount and balance.

Automated checks use only disposable synthetic databases, fake ZenMoney and the
built extension. Real BIDV capture/delivery is a separate acceptance step. Existing
deployment remains untouched until an explicitly authorized release/deployment.
