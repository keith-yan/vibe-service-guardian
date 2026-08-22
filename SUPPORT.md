# 支持范围

## 可以提交 Issue 的问题

- Windows 10/11 x64、macOS 13+，或带图形桌面的主流 Ubuntu/Linux x86_64/aarch64 上可复现的端口、进程、项目归属错误；
- Docker/WSL/Linux 主机分组错误；
- Agent 进程识别误报或漏报；
- 中英文控制台、GPU 厂商/设备识别、安全停止或便携启动问题。

提交前请运行最新版本，并对日志、命令、路径和截图脱敏。不要上传整个 `data` 目录、SQLite 数据库、Agent 会话文件或任何密钥。

## 不作承诺的范围

- 远程主机、Kubernetes、云端 Agent、容器内部进程树的完整归属；
- 绕过系统权限读取其他用户进程；
- 自动结束疑似遗留服务；
- 对 Agent 未公开内部格式的持续兼容；
- 未经实机验证的平台组合。

## 安全漏洞

不要通过公开 Issue 报告安全漏洞。请先阅读 [SECURITY.md](SECURITY.md)，再使用 [GitHub Private Vulnerability Reporting](https://github.com/keith-yan/vibe-service-guardian/security/advisories/new) 私密提交；不得附带未脱敏日志、数据库、路径、账号信息或凭据。
