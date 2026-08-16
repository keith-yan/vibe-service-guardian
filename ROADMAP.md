# Roadmap / 路线图

This roadmap describes intended work, not commitments or completed adoption. Priorities are set by user safety, decision value, evidence quality, and cross-platform correctness.

## 0.8.3 — local Alpha convergence (completed local milestone, not published)

- Explainable service ownership, stale assessment, stop impact, bounded stop verification, and recovery guidance.
- Local model capacity planning, runtime health, fixed workload matrix, and prediction-error calibration.
- Cross-platform code paths and unsigned portable build kits.
- Privacy-minimized local impact confirmation and explicit redacted report export.
- Versioned SQLite migration, dependency locks, public-tree audit, security gates, and release documentation.

Exit criteria: all local automated gates pass; current-host portable path is rebuilt and smoke-tested; unsupported real-platform claims remain explicitly unverified.

## 0.8.4 — bounded daily-use P0 closure (completed local milestone, not published)

- Observe a stopped host service for a user-selected 5/15/30-minute window and report evidence-backed relaunch without a second automatic stop.
- Reuse explicit user lifecycle labels through a one-way executable-path and working-directory identity while preserving every existing stop guard.
- Calibrate an already loaded local model for 60 seconds at concurrency 1 or 2 and persist hardware-bound measured profiles.
- Connect project ownership, inference health, live requests, measured capacity headroom, and prediction error in one view.

Exit criteria: P0-specific tests and the full local gate pass; the Windows portable package is rebuilt and smoke-tested; no P1 feature or target-platform claim is silently included.

## 0.8.5 — attribution evolution P2-A (completed source milestone, unreleased)

- Turn an explicit ownership correction into a deterministic local rule with instance, standard, or strict matching scope.
- Keep the latest five auditable rule versions, deterministic conflict resolution, hit/override evidence, and a unique-service-episode correction rate.
- Export a redacted, integrity-protected JSON rule pack; require preview, conflict disclosure, and explicit service rebinding before import.
- Protect Agent/IDE-managed child processes through visible parent evidence and replace broad Docker inspection with a fixed metadata allowlist that excludes container environment variables.

Exit criteria: schema-6 migration, conflict, rollback, rule-pack, Agent-child, and Docker-privacy tests pass; the full local gate and current-host portable package pass; no P2-B native-platform claim is included.

## 0.8.5.1 — daily-use convergence (current local branch, unreleased)

- Make an action-oriented Today's Focus view the default without removing the complete inventory or changing risk scores.
- Enforce one VSG control-plane process per data directory with a crash-safe Windows/macOS/Linux OS lock and cross-version health identity checks.
- Replace disabled stop affordances on protected/managed services with visible, display-only lifecycle guidance.
- Promote native read-only Ollama, llama.cpp, and vLLM evidence, loaded-model identity, and measured concurrency headroom to the home view.

Exit criteria: targeted and full local gates pass; a second-launch smoke proves port/PID reuse rather than adjacent-port fallback; the current-host UI is checked in both languages; other operating systems remain preview until 0.8.6 native acceptance.

## 0.8.6 — managed-runtime evidence and native acceptance P2-B

- Execute Windows 10/11, Apple Silicon macOS, Intel macOS, Ubuntu/Linux x86_64, and Linux arm64 acceptance on real hardware.
- Strengthen read-only systemd and launchd unit/label evidence only where the native platform exposes it without privilege escalation.
- Validate real Hermes and OpenCode schema variants and downgrade behavior.
- Fix only defects exposed by those matrices; do not add remote control or automatic cleanup.
- Publish a redacted compatibility matrix with exact OS/runtime versions and evidence dates.

Exit criteria: each supported claim has a native evidence record or is downgraded to preview/unsupported.

## 0.9 — public Alpha operations

- Create the public repository, enable CI, issue templates, discussions/feedback routing, and private vulnerability reporting.
- Publish unsigned prereleases with checksums, SBOMs, reproducible commands, and known limitations.
- Collect consented case studies and improve attribution/stale rules from deduplicated outcomes.
- Review anonymized, explicitly contributed rule-pack patterns only after provenance and consent controls are documented; VSG itself remains local and has no synchronization channel.

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
