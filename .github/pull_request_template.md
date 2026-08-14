## 变更内容

说明问题、方案与明确未包含的范围。

## 验证证据

- [ ] `python -m unittest discover -s tests -v`
- [ ] `python scripts/Audit-Public-Tree.py --root .`
- [ ] 涉及平台采集时，附真实目标平台与架构结果
- [ ] 涉及 UI 时，附本地浏览器验证结果

## 安全与隐私

- [ ] 未提交密钥、Token、真实聊天内容、数据库、日志或本机运行数据
- [ ] 新增进程操作仍要求服务端重新校验，且不会自动清理
- [ ] 新增本地元数据读取已在 `PRIVACY.md` 中披露
