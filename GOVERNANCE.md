# Governance / 治理

## Project phase

Vibe Service Guardian is pre-publication and currently governed by one primary maintainer. This is a factual description, not a claim of community governance.

## Decision authority

- The primary maintainer has final authority over scope, security boundaries, releases, and maintainer appointments.
- Routine changes should be small, test-backed, and reversible.
- Changes to process termination, network access, credential handling, log/config reading, snapshots, model workloads, telemetry, or data export require an explicit threat/privacy review in the pull request.
- A feature is not accepted merely because it detects more software; it must improve a user decision without weakening attribution or safety.

## Contribution process after publication

1. Open an issue for behavior changes or security-boundary changes.
2. Submit a focused pull request with tests, validation commands, and evidence level.
3. State whether each platform result is native, fixture-based, code-reviewed only, or unverified.
4. Obtain maintainer review; author self-approval is not sufficient once another maintainer exists.
5. Use the security process for vulnerabilities; do not place exploit details or local user data in public issues.

## Release governance

- Releases are cut only by the designated release authority.
- Version, changelog, dependency locks, tests, security gates, public-tree audit, SBOM, archive contents, checksums, and smoke tests must agree.
- Failed or unexecuted platform acceptance is disclosed, never converted into a supported claim.
- Published artifacts are immutable; fixes use a new version.

## Evidence and claims

- Engineering tests, maintainer self-tests, independent user cases, and public adoption metrics are separate evidence classes.
- Stars, downloads, users, contributors, testimonials, and ecosystem references require a verifiable public source and date.
- The local impact report is self-reported, retention-bounded evidence and cannot be presented as independent adoption.
- Targets in `ROADMAP.md` are not achievements.

## Data and privacy governance

- No automatic telemetry or upload is permitted in the current product scope.
- New persistent fields require a documented purpose, retention rule, deletion path, and export classification.
- Reports intended for sharing must be aggregate-only by default and explicitly user-exported.
- API keys, tokens, passwords, session content, raw logs, VSG databases, and local paths are never acceptable public contribution evidence.

## Conflict and conduct

Behavior is governed by `CODE_OF_CONDUCT.md`. The primary maintainer resolves ordinary disputes. If the dispute concerns that maintainer, a future uninvolved maintainer should review it; until one exists, this limitation must be disclosed rather than hidden.

## Governance changes

Material governance changes must be documented in a reviewed pull request after publication. Maintainer additions and removals update `MAINTAINERS.md` with role scope and date.
