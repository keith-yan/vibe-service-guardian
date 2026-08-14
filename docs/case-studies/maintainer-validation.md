# Maintainer validation: bounded local decision loops

- Evidence class: maintainer self-test
- Independent adopter: no
- Environment: current Windows development workspace plus disposable loopback fixtures
- Consent: project owner’s own engineering record

## Baseline problem

The implementation needed evidence that its highest-risk promises were real behavior rather than mocked success: stopping only the intended fixture process tree, verifying port closure, detecting a managed relaunch without killing it again, and executing a fixed workload matrix without retaining generated content.

## Evidence used

- `tests/test_e2e_stop_verification.py`
- `tests/test_e2e_benchmark_matrix.py`
- `tests/test_storage_migrations.py`
- `tests/test_impact.py`
- `docs/VALIDATION.md`

## Outcome

The disposable fixtures exercise the bounded stop and workload paths through real local processes and HTTP. The storage tests verify migration/recovery behavior. The impact tests verify feedback deduplication, explicit export confirmation, deterministic report hashing, and exclusion of fixture PID, path, IP, command secret, hostname, and session ID from the report.

## What this case does not prove

- It is not an independent user case.
- It does not prove macOS/Linux native behavior.
- The fake runtime does not establish real-model TPS, TTFT, power, temperature, or concurrency.
- It does not establish downloads, popularity, external adoption, or ecosystem importance.

This record belongs to engineering readiness evidence only.
