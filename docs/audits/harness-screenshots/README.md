# OpenMuse TUI 视觉基线

这些截图是 OpenMuse Harness 的离线视觉回归基线，不连接模型、不读取真实运行数据。

## 状态矩阵

每个终端尺寸都应包含以下状态：

- `initial`：OpenMuse 品牌、单一输入入口和空状态
- `running`：当前阶段、已用时和处理中状态
- `completed`：标题、质量结论和结果操作
- `failed`：失败步骤、错误摘要和恢复动作
- `waiting`：候选选题和用户选择请求

当前尺寸：`80×24`、`136×51`、`156×54`。

## 重新生成

在仓库根目录执行：

```bash
python docs/audits/capture_tui_visuals.py
```

脚本使用固定的离线 manifest，截图不会包含绝对路径、API Key 或真实论文数据。README 首屏只引用 `136x51-initial.svg`；其余状态用于维护者检查和回归对比。

## Stage 6 偏好面板

`stage6-80x24-memory.svg`、`stage6-136x51-memory.svg`、`stage6-140x48-memory.svg`
展示待确认偏好、来源、有效期和确认/编辑/忘记操作。生成命令：

```bash
.venv/bin/python docs/audits/capture_memory_visuals.py
```

这些是 Textual 导出的终端渲染截图，不是网页实现。SVG 查看器的中文字体替换可能影响字距，
实际交互和控件边界由对应尺寸的 Textual Pilot 测试验证。
