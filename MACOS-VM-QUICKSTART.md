# macOS 13 AMD/VMware 验收快速说明

本构建验收包用于 `macOS 13+ / x86_64 / AMD 宿主机 VMware` 功能预览验证。
它不能替代 Intel Mac 或 Apple Silicon 真机验收，也不能验证虚拟机未透传的 GPU、温度、风扇与功耗。

## 使用顺序

1. 用 macOS Finder 解压 `.tar.gz` 构建包。
2. 双击 `Run-macOS-VM-Auto-Test.command`，完成环境检查、Python 安装、原生构建和自动验收。
   - 已有兼容的 Python 3.10–3.12 时不会安装或覆盖 Python。
   - 未找到兼容 Python 时，脚本只从 Python Software Foundation 官方地址下载固定的 Python 3.12.10 Universal2 安装包。
   - 下载后同时校验固定 SHA256、文件大小和 Apple 安装包签名。
   - 安装前需要输入 `INSTALL PYTHON 3.12.10`，随后 macOS 会请求一次管理员密码。
3. 看到 `MACOS_VM_AUTOMATIC_ACCEPTANCE_OK` 后，双击 `Start-macOS-Manual-Test.command`。
   - 测试 HTTP 服务只绑定 `127.0.0.1`，端口由 macOS 自动分配，不会再因固定端口被旧进程占用而阻断。
   - 重复双击时，脚本仅在 PID、命令特征和监听端口全部匹配后复用当前构建包的测试服务。
   - 如果后续启动步骤失败，脚本只回收本次刚创建且仍通过三重身份验证的测试服务；不会停止旧版本或其他进程。
4. 按自动打开的 `MANUAL-TEST-CHECKLIST.txt` 完成界面验收。
5. 双击 `Finish-macOS-Manual-Test.command`，按清单中的实际动态端口安全清理并生成验收证据 ZIP。

日志、Python 下载缓存、测试项目和验收证据统一保存在当前构建目录旁边、
以 `.local-state` 结尾的独立目录，不会写入待审计的源码树。
如果旧包曾在源码目录内生成运行日志，新脚本会先将其迁移到该状态目录，再开始构建。

脚本不会停止已有系统服务，不会读取进程环境变量，不会上传遥测，也不会强制终止无法确认身份的 PID。
新构建包不会自动清理旧构建包留下的测试进程；旧进程不会阻断动态端口测试，如需清理应运行对应旧包的收尾脚本。
首次安装 Python 以及构建阶段需要联网。脚本不会使用 `curl | sh`、Homebrew 或未固定版本的第三方 Python。
