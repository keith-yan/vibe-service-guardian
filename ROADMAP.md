# Roadmap / 路线图

This roadmap describes intended work, not commitments or completed adoption. Priorities are set by user safety, decision value, evidence quality, and cross-platform correctness.

## 0.8.3 — local Alpha convergence (current, unreleased)

- Explainable service ownership, stale assessment, stop impact, bounded stop verification, and recovery guidance.
- Local model capacity planning, runtime health, fixed workload matrix, and prediction-error calibration.
- Cross-platform code paths and unsigned portable build kits.
- Privacy-minimized local impact confirmation and explicit redacted report export.
- Versioned SQLite migration, dependency locks, public-tree audit, security gates, and release documentation.

Exit criteria: all local automated gates pass; current-host portable path is rebuilt and smoke-tested; unsupported real-platform claims remain explicitly unverified.

## 0.8.4 — native acceptance and defect closure

- Execute Windows 10/11, Apple Silicon macOS, Intel macOS, Ubuntu/Linux x86_64, and Linux arm64 acceptance on real hardware.
- Validate real Hermes and OpenCode schema variants and downgrade behavior.
- Fix only defects exposed by those matrices; do not add remote control or automatic cleanup.
- Publish a redacted compatibility matrix with exact OS/runtime versions and evidence dates.

Exit criteria: each supported claim has a native evidence record or is downgraded to preview/unsupported.

## 0.9 — public Alpha operations

- Create the public repository, enable CI, issue templates, discussions/feedback routing, and private vulnerability reporting.
- Publish unsigned prereleases with checksums, SBOMs, reproducible commands, and known limitations.
- Collect consented case studies and improve attribution/stale rules from deduplicated outcomes.
- Add importable/exportable rule packs only after signature, provenance, and rollback design is reviewed.

Exit criteria: at least two maintenance cycles, reproducible public checks, and no unresolved critical security finding.

## 1.0 — stable single-user local control plane

- Define compatibility guarantees and database migration support.
- Complete accessibility and localization review.
- Establish signed/notarized distribution only if maintainable certificates and release custody exist.
- Document reliability objectives for collector failure, database recovery, and high-risk-action verification.

Multi-user accounts, remote administration, automatic termination, credential harvesting, LAN discovery, and deliberate OOM probing are out of scope unless a separate threat model and explicit product decision are approved.

## Prioritization rule

Work is ordered by:

1. preventing irreversible or misattributed actions;
2. making unknown evidence visible;
3. closing the user decision loop;
4. improving native platform correctness;
5. only then expanding detection breadth or presentation.
