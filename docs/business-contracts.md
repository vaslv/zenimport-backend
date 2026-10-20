# Shared business contract reference runner

Canonical fixtures belong to ZenImportChrome `contracts/business/v1/`. This
repository consumes the checksum-verified copy under `fixtures/business/v1/`;
`revision.json` pins the manifest and source commit. Never modify only this copy
to make a failing behavior test pass. Change the canonical contract deliberately,
review the expectation, refresh both manifests/copies and run both Docker suites.

`tests/contracts/reference.py` calls actual current Python recipient normalization,
wire validators, signed movement logic and the installed PyPI SDK codec. Independent
Decimal expectations cover arithmetic. `tests/contracts/suite.py` produces observed
results for the cross-language gate. Pure L2 operations make no API requests and
use no application database. Later stages add real stateful adapters.

Run `scripts/test-docker.sh` for all backend checks. With the neighboring Chrome
checkout, run `sh ../ZenImportChrome/scripts/test-business-contracts.sh .` to build
both adapters in Docker and compare their observed outputs. Execution networking
is disabled. Expected results cover eight pure scenarios, 500 arithmetic and
6,354 Unicode 15 vectors. Four future stateful specifications must explicitly fail
as unsupported, rather than silently ignoring state assertions/faults/UUIDs.

Money uses Decimal independently of ambient precision; bank input remains positive
24-integer/12-fractional-digit text with currency-scale checks. The TypeScript
contract bounds pure arithmetic and financial projections at 96 integer/96
fractional digits; unsupported values fail explicitly. Original SDK/raw evidence
is kept separately. Canonical DTO sorting/ASCII escaping uses current
capture_policy.canonical; compact wire fixtures remove formatter whitespace only.

Unicode 15 NFKC + full casefold and Python whitespace are frozen. Prediction keys
with newer/unassigned characters require explicit handling; they must not cause
raw bank history to be lost. Calendar fixtures include local bank days, offset
timestamps, leap days and calendar-month clamping. Deterministic fixture contexts
provide clock/UUID dependencies for future stateful adapters.

The high-precision transfer fixture initially failed against the old source's
80-digit context. Correction 4f30c02 preserves the independent expectation and
adds direct regression tests. See docs/refactor/05-progress.md for Docker evidence.
