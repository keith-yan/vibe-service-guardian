# 贡献指南

感谢你改进 Vibe Service Guardian。项目优先接受可验证、最小权限、不会读取聊天正文的改动。

## 开发环境

需要 Python 3.10 或更高版本：

```text
python -m venv .venv
python -m pip install --upgrade --only-binary=:all: pip==26.2.1
python -m pip install --only-binary=:all: -r requirements.txt
python -m unittest discover -s tests -v
python -m ruff check vsg tests scripts
python -m bandit -r vsg scripts -x tests,build,dist,release -ll
python scripts/Audit-Public-Tree.py --root .
```

Windows 使用 `.venv\Scripts\python.exe`，macOS/Linux 使用 `.venv/bin/python3`。

## 提交要求

- 一个改动解决一个明确问题，并补充失败前、修复后都能解释的测试。
- 新增 Agent 适配必须区分“进程识别”“项目归属”“会话归属”。只有进程名证据时，不得宣称已识别会话。
- 不读取聊天正文、目标进程环境变量、浏览器状态或网络包正文。若确需扩展数据面，必须先更新威胁模型和隐私说明。
- 不提交 `data/`、数据库、日志、构建产物、真实用户名/项目路径、API Key、Token 或密码。
- 进程停止能力必须继续使用 PID、创建时间、进程树、保护名单和人工确认；不接受自动清理或按名称批量结束。
- 平台相关改动要标明是在真实平台验证、自动化夹具验证，还是仅完成代码适配。
- 用户数、下载量、采用、节省时间、误报率和评价等影响力声明必须给出可复核来源、日期、分母和证据等级；本机成效报告只能作为自报案例附件，不能冒充独立采用。

## Pull Request 证据

PR 应列出测试命令及结果。macOS 采集或打包改动必须附 `scripts/Validate-macOS.sh` 的真实 Mac 输出；Windows 打包改动必须附便携 EXE 的本地 HTTP 冒烟结果。无法执行的目标环境必须明确写为“未验证”，不能用代码审查代替实机验收。

安全问题请按 [SECURITY.md](SECURITY.md) 的方式报告，不要在公开 Issue 中提供利用细节。

案例与影响证据按 [IMPACT.md](IMPACT.md)、[docs/EVIDENCE-REGISTER.md](docs/EVIDENCE-REGISTER.md) 和 [docs/case-studies/README.md](docs/case-studies/README.md) 提交。不得上传 `history.sqlite3`、原始日志或未经同意的引语。
