# macOS x86_64 VMware preview validation / macOS x86_64 VMware 预览验收

Date / 日期：2026-08-16
Version / 版本：Vibe Service Guardian 0.8.5.2 r8
Evidence status / 证据状态：Partial preview / 部分预览

## Environment / 环境

- Guest / 客体：macOS 13.7.8, `x86_64`
- Python：3.12.10
- Virtualization / 虚拟化：VMware on an AMD host, as reported by the maintainer; the guest logs do not independently prove the host CPU vendor.
- Distribution / 分发：unsigned portable build with ad-hoc macOS signing; no Developer ID signature or notarization.

本记录只保留脱敏结论和内容散列，不保存用户名、共享目录、绝对路径、PID、运行令牌或原始日志。

## Confirmed / 已确认

- Native build completed and the target emitted `MACOS_NATIVE_VALIDATION_OK` and `MACOS_VM_AUTOMATIC_ACCEPTANCE_OK`.
- Mach-O architecture and the ad-hoc code-signature checks passed on the guest.
- The unit-test suite reported OK; one Windows Git Bash-specific test was skipped by platform.
- The manual-test helper bound a dynamically assigned loopback port; a repeated start reused the same owned helper only after PID, command and listener evidence matched.
- Later state showed the helper PID/port files and VSG `runtime.json` removed.
- Portable ZIP SHA-256: `E41CE43EB8395F8D5CC03527B4A9C1B090B55235DBE5B004B5C927D367BAAA10`.

## Not confirmed / 未确认

- The manual checklist remained an unchecked template and no UI screenshots were supplied. This is not full manual UI acceptance.
- The VM cannot establish Metal, physical GPU/unified-memory behavior, temperature, fan, power, Gatekeeper-on-a-clean-machine, or hardware-specific performance.
- The result does not cover physical Intel Mac hardware or Apple Silicon.
- It does not prove external adoption or long-term stability.

## Evidence defect and remediation / 证据缺陷与修复

The r8 evidence ZIP had SHA-256 `8534A062F6CFEBE45DA032C82333D302E7BCEA2646E29C699F6DE28741E03C56`, but the archive captured the final log while its `tee` writer was still active. The archived log was therefore shorter than the standalone log after script completion. The ZIP is retained only as evidence of the defect, not as a canonical complete acceptance bundle.

r9 changes the workflow to close and wait for the log writer before staging evidence, persists the latest helper status across repeated finish runs, separately verifies VSG shutdown through `runtime.json` removal, and excludes AppleDouble resource files. These paths pass a self-contained Windows Git Bash regression, but r9 still requires a target macOS rerun before its evidence bundle can be accepted.

## Claim boundary / 声明边界

Permitted claim / 可用声明：

> Vibe Service Guardian 0.8.5.2 r8 completed an automated native preview on a macOS 13.7.8 x86_64 AMD-hosted VMware guest.

Prohibited claim / 不可用声明：

> Vibe Service Guardian is fully validated on Intel Mac, Apple Silicon, or all macOS 13+ systems.
