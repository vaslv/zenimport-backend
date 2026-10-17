# Project 3: Chrome extension and bank modules

Goal: read history in an open bank session, send it to the backend, and show one synchronization's progress. Source: `/Users/vaslv/Projects/Vaslv/ZenImport/extension`. Proposed project: `ZenImportChrome`.

Sketch: [three popup states](../zenimport-popup-wireframe.html). It illustrates structure, not a finished UI or automatically verified layout.

## E1. Independent skeleton

Depends on B0. Extract MV3 build, popup, background coordinator, backend client, and bank modules. Do not change bundler/framework without practical need. Remove product SPA/options page/routes, injected UI, category/type controls, toggle, and old send/reconciliation buttons. Data capture remains: removing UI does not mean removing needed content scripts.

Server address/backend token are extension-only; the bank page never receives them. Limit permissions to actually supported banks and the selected server. Unsupported tabs/expired login produce brief popup states without starting another bank's capture.

Acceptance: built extension installs, popup opens, bank DOM has no product elements, old editor is unreachable. Merely opening a bank page never starts a new job.

## E2. Connector contract and VietinBank

Source references: `auto-background.js`, `auto-content.js`, `vietinbank-json.js`, `history-period.js`, `full-sync.js`, `docs/vietinbank-automation.md`. Separate common coordination from bank-specific knowledge.

Connectors provide page support, access context, complete/partial account discovery, capabilities (currencies, time zone, request window, depth), interval/page capture, extra details, normalized records, and completeness evidence. They neither send to ZenMoney nor categorize.

Before claiming multiple-account support, observe the actual list source, switching, detail availability, and stable IDs. Explicitly report incomplete discovery rather than treating one visible row as a full list. Capture anonymized fixtures without auth fields.

Preserve old identity: trxId, otherwise the previous versioned fingerprint and aliases. Never replace hashes without migration. Do not claim USD merely because UI permits an account: the parser currently supports VND; currencies need separate fixtures. Show reasons for discovered unsupported accounts and never import distorted amounts.

Acceptance: normal/empty pages, expired login, wrong account, changed period/total, duplicate rows, missing trxId, exact amounts, counterparty details, supported/unsupported currencies. Also test the common coordinator with a synthetic second bank without implementing an actual integration.

## E3. Resumable capture

Obtain plans from the backend; sequentially read enabled accounts/windows. Use confirmed VietinBank limits, currently up to 30 days. Upload a page, wait for persistence, then advance checkpoints. Job state belongs outside the popup and survives its closure/service worker termination.

On resume, compare server checkpoints and current tab/account/profile. Reject delayed previous-job responses. Complete intervals only after every page/detail. Login expiry/tab closure stops capture without data loss. Ordinary necessary capture requests are permitted; remove continuous simulated activity for session keepalive.

Acceptance: popup closure; worker stop between send/ACK; repeated page; account/tab switch; expired login; cancellation; second-account failure; two extensions on one backend; disabled accounts. The server finishes delivery independently of Chrome.

## E4. Popup

Ordinary screen: name/bank, Synchronize, brief progress/result, collapsed Settings. During capture, the primary button may become Stop capture; this never undoes delivered records. No other financial actions.

First connection: expand server address/backend token when needed; load ZenMoney accounts; assign each bank account a compatible-currency target or Do not import. Unselected differs from ignored. All disabled means no start. Users create new ZenMoney accounts on their phone; the popup refreshes the list.

Period: date 30 days ago, six-calendar-month shortcut, custom nonfuture date. New accounts expose settings on popup opening while other configured accounts can continue. Settings permit mapping changes within B0 restrictions.

One-time old-history capture uses a separate empty date field in settings; nonempty input applies to the next synchronization and clears after complete execution. Ordinary token changes never cause backfill. Long titles, many accounts, keyboard navigation, and short errors must work without horizontal overflow.

Acceptance: form/save; unavailable backend; incompatible version; new account; all ignored; 30 days/6 months/date; reopening; partial result; unavailable target. Test behavior, not precise internal function placement.

## E5. Independent and end-to-end checks

Parsers/periods: unit tests. Popup/worker: browser tests of the actual built extension in isolated Docker. B6/fake ZenMoney integration: two enabled accounts, one ignored; new uncategorized/predicted/possibly matching records; repeat and deletion; lost response; forced rereading.

Output: reproducible extension archive, install/update instructions, actual tested scenarios. Never publish to the Store or treat Docker fixtures as live bank verification. Actual capture/mobile New list belong to M3.
