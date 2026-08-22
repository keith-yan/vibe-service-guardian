# Project impact and evidence / 项目影响与证据

> Status: newly public repository with one published unsigned Alpha prerelease. This document separates verified engineering evidence, local self-reported outcomes, public attention, and independent adoption. Repository/release existence and early GitHub interest are verified; independent adoption is not.

## Why this project exists / 为什么要做

Vibe coding leaves behind more than open ports. After several agent and terminal sessions, a user often cannot answer four basic questions:

1. Who opened this service: a project, an agent session, an IDE, Docker, WSL, or a normal terminal?
2. Is it still needed, and what evidence supports a “stale” judgment?
3. What will break if it is stopped, can it relaunch automatically, and how can it be recovered?
4. Can this machine safely and usefully run the intended open-weight model and workload?

Vibe Service Guardian turns those questions into a local evidence chain: detect → attribute → assess → explicitly act → verify → calibrate → record the human outcome. It is designed to reduce accidental service termination, forgotten listeners, unsafe exposure, and model downloads that cannot meet the intended workload.

## Intended users / 目标用户

- Individual developers using Codex, Claude Code, Cursor, Windsurf, VS Code, WorkBuddy, Hermes, OpenCode, or ordinary terminals.
- Local-AI users operating Ollama, llama.cpp, vLLM, SGLang, MLX-LM, LM Studio, ComfyUI, and related runtimes.
- Maintainers and support engineers who need a reproducible, privacy-minimized explanation of local service ownership and runtime health.

## Evidence model / 证据分层

| Level | What it can support | Current status | What it cannot support |
|---|---|---|---|
| E1 — Engineering verification | The implementation follows bounded control, privacy, migration, and protocol contracts | Available; see `docs/VALIDATION.md` and `docs/EVIDENCE-REGISTER.md` | Real-world adoption or user value |
| E2 — Local self-reported outcomes | One VSG instance recorded whether flagged services were actually stale and whether stop/benchmark verification completed | Supported by the local impact report; no automatic upload | Independent user count or community impact |
| E3 — Independent case evidence | A consenting external user describes the before/after result with reproducible, redacted evidence | Not yet available | Broad adoption unless the sample and method justify it |
| E4 — Public ecosystem evidence | Public releases, unique contributors, issue/PR response, downloads, dependent projects, citations | Public repository plus [`v0.8.5.2-alpha.1`](https://github.com/keith-yan/vibe-service-guardian/releases/tag/v0.8.5.2-alpha.1); 112 Stars at the dated snapshot below | A release, Stars, maintainer PRs, or maintainer verification downloads do not prove users, installations, independent adoption, or ecosystem importance |

## Public repository snapshot / 公开仓库快照（2026-08-23 00:43 UTC+8）

This snapshot was read from GitHub after the first Alpha was published. It is a dated observation, not a live counter or a user estimate.

- Public MIT repository created 2026-08-14; 112 Stars, 0 Forks, and 1 subscriber.
- 11 commits on `main`; 14 pull requests in total, including 8 merged maintainer PRs and 6 closed-unmerged Dependabot PRs; 0 open PRs.
- One contributor is reported: [`keith-yan`](https://github.com/keith-yan), the sole primary maintainer. This is not an independent contributor.
- 0 public Issues and no public issue-response history.
- One published checksum-backed prerelease, [`v0.8.5.2-alpha.1`](https://github.com/keith-yan/vibe-service-guardian/releases/tag/v0.8.5.2-alpha.1), with 6 uploaded files. GitHub reported 0 asset downloads at collection time.
- Maintainer release verification included authenticated digest comparison and anonymous download checks. Any later download counter can include maintainer verification, bots, repeat downloads, and sidecar downloads, so it must not be translated into users or installations.

## Local impact report / 本机成效报告

The Web console exposes a local-only impact preview and an explicit `EXPORT REPORT` JSON export. It aggregates:

- current services, project/agent attribution, stale/review candidates, model runtimes, and non-loopback listeners;
- deduplicated human outcomes: `confirmed_stale`, `not_stale`, or `uncertain`;
- attribution corrections, stop verification outcomes, relaunch detection, benchmark counts, and retained prediction-error samples;
- an explicit evidence-quality and privacy declaration.

One service fingerprint keeps one updateable outcome, so repeated clicks do not inflate the sample count. The export omits PID, paths, IP addresses, commands, session IDs, logs, and model responses. It is not uploaded automatically and still requires human review before external sharing.

The report deliberately states `external_adoption_verified: false`. Removing or changing that statement without independent evidence would be misleading.

## Metrics and interpretation / 指标口径

| Metric | Numerator / source | Correct interpretation |
|---|---|---|
| Human outcome total | Unique service fingerprints with a retained result | Number of locally reviewed service identities, not number of users |
| Assessment agreement | Decisive human outcomes consistent with the risk class at confirmation time | A local rule-quality signal; subjective and retention-bounded |
| Verified stops | Rows in `stop_verifications` | Explicit stop attempts with bounded post-action observation, not all services cleaned up |
| Relaunch detected | Stop verification observed a replacement PID or listener | Evidence that a lifecycle manager may own the service; VSG does not stop it again |
| Prediction error | Absolute error from mapped, measured workload samples | Local calibration evidence only; not a universal engine benchmark |
| Attribution correction | Successful local correction action | A signal that automatic attribution needed human repair, not necessarily a defect |

## Evidence available today / 当前已有证据

- Disposable process fixtures exercise real process-tree termination, port release, and managed relaunch detection without targeting unrelated user services.
- A loopback fake model runtime executes the fixed 5 + 10 + 20 request workload matrix and verifies response-body non-persistence.
- SQLite migrations are versioned, backed up, transactionally applied, and corruption-tested.
- Security, privacy, public-tree, dependency, and cross-platform build contracts are documented and automated where the current Windows host can validate them.
- A local outcome/export path now exists so future users can produce redacted, reviewable evidence instead of anecdotal claims.
- The public repository and first unsigned Alpha exist at [`keith-yan/vibe-service-guardian`](https://github.com/keith-yan/vibe-service-guardian) and [`v0.8.5.2-alpha.1`](https://github.com/keith-yan/vibe-service-guardian/releases/tag/v0.8.5.2-alpha.1). The release has 6 allowlisted assets, matching remote SHA-256 digests, a Windows SPDX SBOM, explicit platform limitations, and no Linux binary claim.

This is maintenance, release-integrity, and early-interest evidence. It is not evidence of independent adoption or broad public use.

## Evidence still missing / 尚缺证据

- No verified external user, installation, independently attributable download, dependent project, citation, or independent contributor. GitHub counters must not be interpreted as adoption without context.
- No consented external case study or independently verified user outcome.
- No native Apple Silicon, Intel macOS, Linux x86_64, or Linux arm64 acceptance result from this Windows workspace.
- No public issue-response history and only one prerelease; a sustained release/maintenance cadence is not yet established.

## How to add a real case / 如何补充真实案例

Use [`docs/case-studies/README.md`](docs/case-studies/README.md). A valid case must:

1. state whether the author is the maintainer, an independent user, or an organization;
2. describe the baseline and the decision VSG changed;
3. include redacted, reproducible evidence and the measurement window;
4. record limitations and counterfactual explanations;
5. have explicit permission for the quoted or attributed material;
6. never include databases, logs, local paths, account data, credentials, or session content.

## Post-publication targets — not achievements / 公开后的目标，不是现状

- Obtain three consented independent case studies across at least two operating systems.
- Publish native acceptance results for every platform/architecture claimed as tested.
- Measure false-positive/false-negative review outcomes with the documented denominator.
- Establish a visible release cadence and issue/PR response history.
- Record ecosystem references only when a public URL can be independently verified.

These are roadmap targets. They must not be copied into an application as completed facts.
