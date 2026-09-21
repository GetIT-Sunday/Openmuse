# CLI Reference

Run `smearglepaper` without arguments to open the interactive Agent console.

```bash
smearglepaper run paper-research --topic agents
smearglepaper run paper-research --offline-example --workspace ./demo-workspace
smearglepaper run paper-to-article --paper-url https://arxiv.org/abs/1706.03762
smearglepaper run paper-to-wechat --paper-url URL --real
smearglepaper agent "解读这篇论文并生成公众号草稿" --paper-url URL
smearglepaper runs list --json
smearglepaper runs show RUN_ID --events --json
smearglepaper runs resume RUN_ID
smearglepaper runs retry RUN_ID --step review
smearglepaper runs cancel RUN_ID
smearglepaper artifacts list RUN_ID --json
smearglepaper config check --json
```

进入 TUI 后也可以直接配置模型，不需要退出编辑器或手动编辑 `.env`：

```text
/connect       打开模型连接设置，填写 Base URL、模型和 API Key
/model         打开模型选择器（也可使用 /model MODEL 临时切换）
/session       打开会话管理器
/session new   新建会话
/resume        安全继续未完成的对话，复用已保存的工具结果
/memory        确认、编辑或忘记跨会话写作偏好
/memory off    暂停使用偏好（on 重新启用）
/forget ID     忘记一条偏好；不带 ID 时仅清空本会话上下文
/model MODEL   只切换当前会话使用的模型
```

`/connect` 的“保存并测试”会发送一次最小连通性请求。API Key 只保存在
本机项目 `.env`（权限为 `0600`），不会写入会话消息、运行日志或提交到仓库。
如果只想先保存配置，可以选择“仅保存”。

偏好记忆需要用户确认才会跨会话使用；来源、范围和有效期可在面板查看。
它不是发布授权，也不会自动保存论文正文。详见 [偏好记忆](MEMORY.md)。

New automation commands emit their machine result on stdout. Live progress is sent to stderr unless `--json` is used. Exit codes are: `0` success, `2` invalid arguments, `3` invalid configuration, `4` external service failure, `5` workflow failure, `6` quality gate failure, `7` waiting for approval, `8` waiting for selection, `9` external result needs verification, and `130` cancellation.

若外部操作超时或中断且结果未知，请先在目标服务核对，再使用
`runs reconcile RUN_ID --executed --external-id REMOTE_ID --confirmed` 或
`runs reconcile RUN_ID --not-executed --confirmed` 记录结果。此命令不会自动执行，
也不会代替发布审批。详见 [安全恢复说明](RUNTIME.md#interrupted-conversations-and-external-writes)。

The original atomic commands remain available in 0.2. The previous natural-language ProductTask command moved from `agent` to `product-agent`.

维护者可在源码仓库运行 `.venv/bin/python scripts/evaluate_harness.py`，生成 Harness
六项离线验收报告。这不是模型聊天命令，也不会调用真实发布服务。
详见 [Stage 7 验收说明](HARNESS_EVALUATION.md)。
