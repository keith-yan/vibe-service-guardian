# 公开发布与 Codex for OSS 准备清单

本文只是一份本地操作清单。创建 Git 仓库、推送、发布 Release 和提交申请均由维护者本人执行。

## 公开前必须完成

- [ ] 逐文件复核许可证、版权归属和第三方素材来源。
- [ ] 运行 `python scripts/Audit-Public-Tree.py --root .`，确认没有数据库、日志、运行数据、密钥、私有路径或构建缓存。
- [ ] 运行全部单元测试与依赖漏洞审计。
- [ ] 在 Windows 10、Windows 11、Apple Silicon macOS、Intel macOS、Ubuntu/Linux x86_64 与 aarch64 图形桌面分别记录真实验收；无法获得的组合明确标为未验证。
- [ ] 在真实 Hermes 与 OpenCode 中验证进程、项目、显式恢复会话和无目录元数据的降级行为。
- [ ] 由维护者选择 GitHub 用户名/组织、仓库名、公开安全联系方式和维护策略。
- [ ] 在 `MAINTAINERS.md` 填写与公开 GitHub 身份一致的核心维护者信息；复核 `GOVERNANCE.md` 与 `ROADMAP.md`。
- [ ] 复核 `IMPACT.md` 与证据登记表：目标不得写成成就，本机报告不得写成独立采用；没有真实案例时保持“缺失”。
- [ ] 初始化 Git 后检查首个提交内容；`release/`、`data/`、`.venv/`、`build/`、`dist/` 不应进入历史。
- [ ] 推送后确认 CI 全绿，再创建无身份签名的预发布；为每个资产提供 SHA-256 与生成的 SBOM。

## OpenAI Codex for OSS 的官方条件

官方介绍与申请表说明，该计划面向核心维护者或广泛使用的公开项目，也允许对生态重要但不完全符合典型指标的项目解释原因。审核信号包括仓库使用情况、生态重要性和持续维护证据。申请表要求公开 GitHub 用户资料、公开仓库 URL、主要/核心维护者角色说明、项目资格理由、OpenAI Organization ID，以及 API credits 用途；相关自由文本字段上限为 500 字符。

条款还要求有效 ChatGPT 账号和准确、完整的身份/仓库/维护角色信息。OpenAI 可能核验身份、仓库关系、维护者身份或控制权；申请不保证获选。Codex Security/API credits 可能另行审核，且只能用于申请者拥有、维护或获授权管理的仓库。申请材料不应包含机密信息。

官方入口：

- [项目介绍](https://developers.openai.com/community/codex-for-oss)
- [申请表](https://openai.com/form/codex-for-oss/)
- [项目条款](https://learn.chatgpt.com/docs/codex-for-oss-terms)

## 当前项目的客观缺口

本地代码质量文件不能替代公开维护证据。仓库尚未公开时，不存在可核验的 stars、下载量、Issue/PR 处理、版本发布、社区采用或维护历史；这仍是申请竞争力的实质阻断项，不能通过填写文案修复。建议先完成公开发布和数轮真实维护，再以可验证数据说明价值。

当前代码已经提供显式、脱敏、按服务指纹去重的本机成效报告，但它只解决“如何以后收集自报结果”，不等于现在已经拥有外部用户证据。至少应再获得经同意的独立案例，并按 `docs/case-studies/README.md` 记录证据等级、测量窗口、分母和限制。

申请叙事应聚焦真实维护工作：跨平台采集兼容、Agent schema 漂移、误杀防护、隐私最小化、依赖审计、用户问题分诊与发布验证。不要把“代码能运行”包装成“广泛采用”，也不要预测获选概率。
