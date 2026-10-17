# Local first-release readiness — October 17, 2026

Publication had not yet occurred at this checkpoint. The user confirmed MIT and
the three repository URLs; the clarified SDK name is **zenmoney-python**, import
zenmoney_api. Initial GitHub licenses are preserved, remotes configured, and
histories joined through ordinary local merges without rewriting, force, push, or tags.

| Project | Check | Result |
|---|---|---|
| zenmoney-python 0.7.0 | Installed wheel, strict mypy, unit/mock HTTP | 260 passed, 99% coverage |
| SDK artifacts | Wheel from sdist, strict Twine, metadata/LICENSE/py.typed, SHA256 | Passed |
| Backend 0.1.0 + SDK 0.7.0 | Linux ARM64 | 492 passed; lifecycle/HTTP/SIGKILL/graceful/snapshot passed |
| Backend 0.1.0 + SDK 0.7.0 | Emulated Linux AMD64 | 492 passed; same lifecycle checks passed |
| compose.server.yaml, prebuilt image | Both architectures | Private bootstrap, no-overwrite, UID/read-only root, localhost bind, runtime layout/SDK, OCI metadata, volume recreation passed |
| Chrome 0.1.9 | Docker unit/browser | 31 unit + 10 browser passed; one expected skip without backend |
| Chrome + current backend/SDK | Docker integration | 17 passed |
| Chrome ZIP | Two builds, contents | Identical SHA256, LICENSE/PRIVACY, no local settings |
| GitHub workflows | actionlint | Passed; not yet run on GitHub |
| Caddy example | Validation in a network-isolated container | Valid configuration |

ARM64 ran natively in OrbStack on an ARM host; AMD64 used emulation. Every financial
test in this stage used synthetic data/SDK responses. Production containers, SQLite,
bank session, ZenMoney tokens/data, and the user profile were unchanged. Known local
tokens were read only for local Git-object comparisons; values were neither printed
nor passed to the scanner/network. Gitleaks 8.30.1 scans history/prepared diffs with redaction.

The backend installs the **separate package** zenmoney-python==0.7.0 with SHA256,
no-index/no-deps/require-hashes and pip check. Before first release, a local SDK wheel
from ba17ce3 was used. After SDK publication, download the wheel/manifest from the
verified GitHub Release and repeat update_sdk.py. GitHub is not PyPI; this approach
does not require PyPI publication.

CI and gitleaks images are SHA-pinned. Validation used:

- actionlint: `rhysd/actionlint@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667`;
- gitleaks: `ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`;
- Caddy: `caddy@sha256:685ae3d7c34d9b5f8caeddc5d14051db7bbd88ff1f5a2fa0c16bd8ca328fb7af`.

Private logs stay in each project's `.local/publication/`, excluded from Git/artifacts.
The final backend excludes .env, SQLite, tests, browser, SDK checkout, and live runner.
Python/Chrome archives exclude local tokens and history.

Release the SDK first, then backend/GHCR and Chrome. Publication requires separate
explicit permissions, including pushes and Git tags. No claim is made that a public
registry manifest, actual remote server/DNS/HTTPS, or mobile UI has been validated.
For real migration, preserve the delivery journal by transferring the database;
an empty-database test cannot guarantee preservation of deletions unknown to it.

[Release procedure](publication.md) · [Remote server](server-deployment.md)
