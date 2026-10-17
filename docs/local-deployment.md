# Initial local deployment — October 16, 2026

At the user's explicit direction, legacy storage was not migrated. The user deferred separate live mobile acceptance and separately authorized startup of the new backend with a clean database. This is not M4 migration under the previous scenario.

This is a historical snapshot of the initial deployment. Subsequently, Chrome 0.1.8 capture and delivery were verified against actual VietinBank, duplicates were fixed, and synchronization was repeated. The current outcome is in the latest section of the [acceptance log](refactor/05-progress.md). Pre-creation matching is described in [delivery rules](delivery.md); the initial limitations below do not describe the current implementation.

## State at initial deployment

- Legacy container zenimport-backend-1 stopped, restart=no. Port 18791 no longer serves new requests. Its old volume is not connected to the new service; no backup/conversion was performed for this cutover.
- New container zenimport-backend-backend-1 runs at http://127.0.0.1:18792, health=healthy. Persistent volume zenimport-backend-data; read-only root filesystem, UID/GID 10001, one worker, localhost access only.
- .env created with mode 600, excluded from Git and the image. API_TOKEN regenerated; the previous valid ZENMONEY_TOKEN transferred directly into server configuration without printing it in chat. SDK 0.6.0.
- Created clean schema v5: bank transaction/delivery/job tables and mirror were empty before retrieval. A read-only request then fetched current ZenMoney: 115 accounts, 29,124 transactions, 32,672 total entities. The catalog returns 60 active regular accounts, including 10 VND; /api/v1/info: api_version=1, zenmoney_state=ready.
- After mirror retrieval, bank_accounts, bank_transactions, capture_jobs, deliveries, and delivery_attempts remain empty. No creation/updates/deletions in ZenMoney were initiated.

## Extension

Address: http://127.0.0.1:18792. Token: API_TOKEN. Private file data/extension-connection.txt contains only these two settings (600), not the ZenMoney token. In the new extension, open Settings, fill in the fields, click Connect, open the desired bank history, and choose the corresponding ZenMoney account and start date. The user's Synchronize button authorizes delivery.

A clean database contains no previous receipts/links/exclusions or categorization examples. Fetching the ZenMoney mirror alone does not reconstruct bank identities. Historical bank transactions may be added again even when similar ones already exist in ZenMoney; choose the initial import period deliberately. Confirmed category history will accumulate again. Subsequent mobile changes and deletions of records known to the new server take precedence.

## Connection-time fix

The actual Instrument uses shortTitle as the currency code, as specified by the [official documentation](https://github.com/zenmoney/ZenPlugins/wiki/ZenMoney-API#instrument). accounts.catalog and category evidence previously searched for a nonexistent code field. Both locations and synthetic fixtures were fixed. 353 Docker checks passed without external networking; after rebuilding, the real read-only catalog returns 60 accounts. Database schema/API contract are unchanged.

At initial deployment, production mobile checks of viewed=false/edits/deletions and a complete Chrome connector run against the real bank had not been performed. Complete Chrome 0.1.8 capture and delivery were confirmed later; this does not replace the deferred mobile application behavior checks.
