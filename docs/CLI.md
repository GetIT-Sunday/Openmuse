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
/model MODEL   只切换当前会话使用的模型
```

`/connect` 的“保存并测试”会发送一次最小连通性请求。API Key 只保存在
本机项目 `.env`（权限为 `0600`），不会写入会话消息、运行日志或提交到仓库。
如果只想先保存配置，可以选择“仅保存”。

New automation commands emit their machine result on stdout. Live progress is sent to stderr unless `--json` is used. Exit codes are: `0` success, `2` invalid arguments, `3` invalid configuration, `4` external service failure, `5` workflow failure, `6` quality gate failure, `7` waiting for approval, and `130` cancellation.

The original atomic commands remain available in 0.2. The previous natural-language ProductTask command moved from `agent` to `product-agent`.
