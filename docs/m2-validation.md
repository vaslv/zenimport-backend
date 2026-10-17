# M2 — final matrix, October 16, 2026

Backend: 352 checks and lifecycle scenarios (HTTP/auth, restart, SIGKILL, graceful stop, snapshot/restore) passed on ARM64 and linux/amd64. AMD64 ran under OrbStack emulation, not on a physical x86 server. Chrome: 13 unit and nine browser/integration tests (three popup and six integration) using actual built JavaScript, HTTP, SQLite backend, and a stateful fake SDK. Test containers have no external networking. Browser matrix details: neighboring ZenImportChrome/docs/testing.md.

The restored real M1 copy ran in the ordinary runtime without an SDK token, with network none. /health and /api/v1/info passed; zenmoney_state=not_configured. After a graceful stop, the fingerprint of 21 financial tables was unchanged: 073d1abf249cedd3c084038b062d3d5e822cf67908f760acfa691f55e5a8ef8b. Eleven prepared records were not sent. The original production volume was not mounted into this runtime.

Reproduction: scripts/test-restored-copy.sh <private data/rehearsal-...> <local runtime image>. scripts/check-financial-copy.py outputs only hashes and counts. Mutable runtime/mirror freshness need not be unchanged; M1 independently verified the complete source archive.

After validation, large legacy-copy/converted/restored files were compressed to .sqlite3.gz; uncompressed SHA256 values were checked again, and only created uncompressed copies were removed. The archives.json manifest stays beside the private copies, outside Git. Before rerunning, extract the required files into a new private directory; never substitute the original production volume for a copy.

M3 (live bank/mobile acceptance) and M4 (final cutover) are incomplete. These tests do not confirm external HTTPS, actual bank account listing/switching, or mobile viewed=false display.
