# Evidence register / 证据登记表

This register prevents code verification from being misrepresented as user adoption. Update it when evidence is added, superseded, or invalidated.

| ID | Evidence class | Claim supported | Source | Status | Key limitation |
|---|---|---|---|---|---|
| VSG-E001 | Engineering | Local unit/integration contracts pass on the current Windows workspace | `docs/VALIDATION.md` | Available | Does not prove native macOS/Linux behavior or adoption |
| VSG-E002 | Engineering | Stop verification observes process-tree exit, port closure, and managed relaunch without a second stop | `tests/test_e2e_stop_verification.py` | Available | Disposable loopback fixture only |
| VSG-E003 | Engineering | Fixed 5 + 10 + 20 request matrix executes real loopback HTTP and does not persist response text | `tests/test_e2e_benchmark_matrix.py` | Available | Fake runtime; no real model/GPU performance claim |
| VSG-E004 | Engineering | Old databases are backed up and migrated transactionally; corrupt files are quarantined | `tests/test_storage_migrations.py` | Available | Current-host SQLite behavior |
| VSG-E005 | Product evidence path | Human stale/not-stale/uncertain outcomes are deduplicated and can be explicitly exported as aggregate JSON | `tests/test_impact.py`, local Web console | Available | Local self-report, retention-bounded, no automatic upload |
| VSG-E006 | Maintainer self-test | Current owner can reproduce bounded end-to-end behavior | `docs/case-studies/maintainer-validation.md` | Available | Not an independent adopter |
| VSG-E007 | Native platform | Apple Silicon and Intel macOS acceptance | `MACOS-VALIDATION.md` | Missing | Must run on real Macs |
| VSG-E008 | Native platform | Linux x86_64 and arm64 graphical acceptance | `LINUX-VALIDATION.md` | Missing | Must run on real target machines |
| VSG-E009 | Independent outcome | External user case study with consent and redacted evidence | `docs/case-studies/` | Missing | Cannot be replaced by maintainer testing |
| VSG-E010 | Public ecosystem | Public releases, contributors, issues/PRs, downloads, dependents, citations | [`keith-yan/vibe-service-guardian`](https://github.com/keith-yan/vibe-service-guardian) | Partial | Repository existence proves publication/control only; no release or adoption evidence yet |

## Rules

- “Available” means the linked artifact exists and its stated validation ran; it does not widen the claim beyond the limitation column.
- “Partial” means a source exists but supports only the explicitly stated subset of a broader claim.
- External quotes, usage counts, and case studies require source URL or written consent, collection date, and denominator.
- Never add raw `data/`, SQLite databases, logs, paths, IP addresses, credentials, session IDs, or model responses.
- If evidence becomes stale or invalid, mark it superseded/invalidated; do not silently delete the history after publication.
