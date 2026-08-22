# 公开发布与 Codex for OSS 准备清单

本文是一份发布操作清单，不会自动推送、创建标签、发布 Release 或提交申请；这些远程操作均由维护者明确确认后执行。

## 首次 Alpha 与后续公开发布必须核对

- [ ] 逐文件复核许可证、版权归属和第三方素材来源。
- [x] 运行 `python scripts/Audit-Public-Tree.py --root .`，确认没有数据库、日志、运行数据、密钥、私有路径或构建缓存。
- [x] 运行全部单元测试与依赖漏洞审计；本轮元数据修改的本地发布门禁已通过。合并后的最终 `main` CI 仍由后续未勾选步骤复核。
- [ ] 在 Windows 10、Windows 11、Apple Silicon macOS、Intel macOS、Ubuntu/Linux x86_64 与 aarch64 图形桌面分别记录真实验收；无法获得的组合明确标为未验证。
- [ ] 在真实 Hermes 与 OpenCode 中验证进程、项目、显式恢复会话和无目录元数据的降级行为。
- [x] 由维护者选择 GitHub 用户名、公开仓库、维护策略和非秘密安全渠道；仓库使用 `keith-yan` 身份及 GitHub Private Vulnerability Reporting。
- [x] 在 `MAINTAINERS.md` 填写与公开 GitHub 身份一致的核心维护者信息；已复核 `GOVERNANCE.md` 与 `ROADMAP.md`。
- [x] 启用 GitHub Private Vulnerability Reporting，并确认 `SECURITY.md`、`SUPPORT.md` 与维护者清单指向同一私密入口。
- [x] 复核 `IMPACT.md` 与证据登记表：目标没有写成成就，本机报告没有冒充独立采用，尚无真实案例的项目继续保持“缺失”。
- [x] 检查公开 Git 历史与当前树；`release/`、`data/`、`.venv/`、`build/`、`dist/` 均不进入版本历史。
- [x] 元数据提交合并后，最终 `main@0093355` 的 10 项 CI 全绿并重新构建 6 个计划上传文件；每个文件使用精确白名单和同名 SHA-256。包含预构建 Windows EXE 的 ZIP 内含许可证清单与 SPDX SBOM；macOS 源码构建包在目标机生成二进制时生成对应许可证与 SBOM。
- [x] 基于最终 `main@0093355` 使用 [`GITHUB-RELEASE-0.8.5.2-alpha.1.md`](GITHUB-RELEASE-0.8.5.2-alpha.1.md) 复核并发布 [`v0.8.5.2-alpha.1`](https://github.com/keith-yan/vibe-service-guardian/releases/tag/v0.8.5.2-alpha.1)：`Prerelease=true`、`Latest=false`、6 个远程资产摘要匹配、Linux 包未上传；已通过匿名公开页面和匿名下载回读。

## OpenAI Codex for OSS 的官方条件

官方介绍与申请表说明，该计划面向核心维护者或广泛使用的公开项目，也允许对生态重要但不完全符合典型指标的项目解释原因。审核信号包括仓库使用情况、生态重要性和持续维护证据。申请表要求公开 GitHub 用户资料、公开仓库 URL、主要/核心维护者角色说明、项目资格理由、OpenAI Organization ID，以及 API credits 用途；相关自由文本字段上限为 500 字符。

条款还要求有效 ChatGPT 账号和准确、完整的身份/仓库/维护角色信息。OpenAI 可能核验身份、仓库关系、维护者身份或控制权；申请不保证获选。Codex Security/API credits 可能另行审核，且只能用于申请者拥有、维护或获授权管理的仓库。申请材料不应包含机密信息。

官方入口：

- [项目介绍](https://developers.openai.com/community/codex-for-oss)
- [申请表](https://openai.com/form/codex-for-oss/)
- [项目条款](https://learn.chatgpt.com/docs/codex-for-oss-terms)

## 当前项目的客观缺口

本地代码质量文件不能替代公开维护证据。项目现已建立公开仓库、可核验的 PR 合并记录和一个带校验资产的 Alpha prerelease；截至 2026-08-23 00:43（UTC+8），GitHub API记录资产下载为 0。项目仍没有可归因的外部用户、安装、独立贡献者、持续 Issue/PR 处理周期或独立社区采用证据；这仍是申请竞争力的实质缺口，不能通过填写文案修复。后续只能用带日期、来源和限制的真实维护数据逐步补足。

当前代码已经提供显式、脱敏、按服务指纹去重的本机成效报告，但它只解决“如何以后收集自报结果”，不等于现在已经拥有外部用户证据。至少应再获得经同意的独立案例，并按 `docs/case-studies/README.md` 记录证据等级、测量窗口、分母和限制。

申请叙事应聚焦真实维护工作：跨平台采集兼容、Agent schema 漂移、误杀防护、隐私最小化、依赖审计、用户问题分诊与发布验证。不要把“代码能运行”包装成“广泛采用”，也不要预测获选概率。
