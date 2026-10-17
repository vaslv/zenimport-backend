# ZenImport: refactoring handoff

Date: October 15, 2026. Status: implementation authorized by the user; B0, S1, and B1–B5 complete. The independent B6 portion is complete; E5 integration follows extension readiness. Next stage: E1.

## Read in small portions

Start with this file and `00-contracts.md`, then open only the relevant project plan. The complete chat history is unnecessary. Detailed decision context: [design](../zenimport-refactor-design-2026-10-15.md); consult it for ambiguity rather than repeating it in every task.

On October 15, the user requested sequential implementation with regular local Git commits. The design is accepted as the implementation basis, including preparation of the proposed copy-based migration. Production cutover follows M1–M3 validation; the specific set of actual test writes still requires separate agreement.

## Projects and documents

| Project | Plan | Source / proposed location |
|---|---|---|
| ZenMoney SDK | [01-sdk.md](01-sdk.md) | `/Users/vaslv/Projects/Vaslv/ZenMoneyAPI`, already exists |
| Backend | [02-backend.md](02-backend.md) | Independent `/Users/vaslv/Projects/Vaslv/ZenImportBackend`; legacy source remains in ZenImport |
| Chrome and bank modules | [03-extension.md](03-extension.md) | Source `/Users/vaslv/Projects/Vaslv/ZenImport/extension`; new independent project `ZenImportChrome` |
| Migration and overall acceptance | [04-migration.md](04-migration.md) | After backend and Chrome readiness |

ZenImportBackend was extracted into an independent neighboring repository in B1. ZenImportChrome has not yet been created at this checkpoint. Preserve original Git history; do not rename/delete the original repository as an extraction side effect.

Verified source baseline: ZenImport `9271b3b`, SDK pinned as wheel 0.6.0. Check current versions/uncommitted changes before working. `outputs/zenimport-current` and `outputs/zenbridge` copies are not the working source.

## Execution order

1. B0: clarify and fix cross-project contracts. S1: audit the existing SDK.
2. B1–B2: extract the backend and prepare storage/migration on a synthetic database.
3. B3–B5: history ingestion, mirror, learning, reliable delivery queue.
4. E1–E3: extract extension/VietinBank connector and multiple-account capture. E4: compact popup.
5. B6 + E5: independent Docker checks and joint fake-ZenMoney validation.
6. M1–M4: migration rehearsal, real bank/mobile checks, agreed cutover.

One implementation request covers one item, such as B3, rather than the entire refactor. Write documentation/tests with each change. After a stage, update `05-progress.md`: completed work, evidence, open questions, next step. Successful tests of an earlier commit do not validate new changes.

## Model allocation

Practical recommendation: GPT‑6.1 Sol for primary implementation; GPT‑6 Luna High for bounded tasks after contracts are fixed (popup, fixtures, documentation, individual tests); Astra for final migration/identity/queue review when complex contradictions arise. This guarantees neither results nor specific savings.

The recommendation follows the [official model description](https://learn.chatgpt.com/docs/models). [Usage limits](https://learn.chatgpt.com/docs/pricing) also depend on context, complexity, reasoning, tools, and caching. Do not pass this large conversation into every new task or launch parallel agents unnecessarily. The exact savings percentage is unknown.

## Initial implementation prompt

> Read README.md and 00-contracts.md in this set, then the selected project plan and current 05-progress.md. Implement stage [ID] only. Before changes, read applicable AGENTS.md and check current source. Do not restore browser editors or reconciliation. Validate in Docker with isolated data. Update the execution log with actually performed checks. If a stage depends on an undefined contract or unverified bank behavior, establish and record that fact first. Do not modify production databases, send real transactions, create Git tags, or push without separate permission.

Use a new ordinary chat with these files for an independent stage; forking the full conversation is unnecessary. This document set created no chats and switched no models.

After B1, backend stages belong to ZenImportBackend. Next: E1. Startup/recovery: [operations.md](../operations.md), platform checks: [b6-validation.md](../b6-validation.md). Delivery: [delivery.md](../delivery.md). Mirror/prediction: [mirror-predictions.md](../mirror-predictions.md). Detailed extraction map: [extraction-map.md](../extraction-map.md); actual Docker results: [b1-validation.md](../b1-validation.md).
