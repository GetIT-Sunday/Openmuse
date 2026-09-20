<a name="openmuse"></a><a name="smearglepaper"></a>
<p align="center">
  <img src="assets/banner.png" alt="SmearglePaper banner" width="100%">
</p>

<p align="center">
  <h1 align="center">OpenMuse</h1>
  <p align="center">
    <strong>AI 论文自动化写作与发布助手</strong><br>
    <em>Automated AI Paper Writing & Publishing Assistant</em>
  </p>
  <p align="center">
    <a href="#-功能特性">功能特性</a> •
    <a href="#-快速开始">快速开始</a> •
    <a href="#-安装">安装</a> •
    <a href="#%EF%B8%8F-cli-命令">CLI 命令</a> •
    <a href="#-mcp-服务">MCP 服务</a> •
    <a href="#%EF%B8%8F-配置">配置</a>
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.2.0-blue?style=flat-square" alt="Version">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-yellow?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/arXiv-papers-orange?style=flat-square" alt="arXiv">
  <img src="https://img.shields.io/badge/WeChat-公众号-07C160?style=flat-square" alt="WeChat">
  <img src="https://img.shields.io/github/stars/GetIT-Sunday/SmearglePaper?style=social" alt="Stars">
</p>

<p align="center">
  <strong>中文</strong> | <a href="README_EN.md">English</a>
</p>

---

## ✨ 功能特性

OpenMuse 是一个可扩展的 AIGC Harness，用模型、工具、记忆和 Pack 协同完成内容生产。当前的第一个官方 Pack 是 AutoWechat：把研究论文转成可发布的中文公众号文章。

当前 Python 模块和 `smearglepaper` 命令作为兼容入口保留；新安装也提供 `openmuse` 命令。

<table>
  <tr>
    <td width="50%">
      <h3>📄 论文收集</h3>
      <ul>
        <li>从 arXiv 收集最新论文</li>
        <li>从 AI 工程 RSS/Atom 订阅源收集博客</li>
        <li>从 GitHub Trending 收集热门项目</li>
        <li>按主题相关性、新鲜度和 AI 类别信号排序</li>
      </ul>
    </td>
    <td width="50%">
      <h3>📝 文章生成</h3>
      <ul>
        <li>使用 PyMuPDF 下载解析 PDF</li>
        <li>提取图表候选</li>
        <li>用 LLM 生成中文深度解读文章</li>
        <li>支持 DeepSeek / OpenAI 兼容 API</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>🔍 质量审查</h3>
      <ul>
        <li>文章质量评分</li>
        <li>LLM 编辑优化</li>
        <li>本地模板兜底（离线可用）</li>
        <li>Evidence-first 写作流程</li>
      </ul>
    </td>
    <td width="50%">
      <h3>📱 微信发布</h3>
      <ul>
        <li>生成微信公众号兼容 HTML</li>
        <li>自动创建封面图</li>
        <li>创建 / 更新微信草稿</li>
        <li>MCP 工具支持，可与 AI Agent 集成</li>
      </ul>
    </td>
  </tr>
</table>

<div align="right"><a href="#smearglepaper">↑ 返回顶部</a></div>

---

## 🚀 快速开始

### Agent 原生 Runtime（0.2）

无参数启动 OpenCode 风格交互工作台；CLI、TUI 与 MCP 共用同一个可恢复 Runtime：

```bash
smearglepaper
smearglepaper run paper-to-article --paper-url https://arxiv.org/abs/1706.03762
smearglepaper agent "解读这篇论文并生成公众号草稿" --paper-url https://arxiv.org/abs/1706.03762
smearglepaper run paper-research --offline-example --workspace ./demo-workspace
smearglepaper runs list --json
```

TUI 以“论文或主题 → 候选确认 → 文章 → 对话修改 → 手机预览”为主流程。主题输入会展示 3 篇候选论文；粘贴论文链接会直接进入阅读与写作。文章完成后按 `Ctrl+O` 打开手机尺寸预览，后续修改会自动刷新。

每次执行都会保存 `manifest.json`、顺序事件流、检查点与带哈希的 Artifact。真实微信写入默认禁用；使用 `--real` 后仍会停在审批门禁，需显式执行 `smearglepaper approve RUN_ID approve-publish`。详见 [CLI](docs/CLI.md) 与 [Runtime](docs/RUNTIME.md)。

**① 一键运行完整流水线**

```bash
smearglepaper agent-run --topic agents --days 30 --top-k 5
```

**② 分步运行**

```bash
smearglepaper preflight                                          # 检查环境
smearglepaper collect --topic agents --days 30 --max-results 50 # 收集论文
smearglepaper rank --top-k 5                                     # 排序论文
smearglepaper write --paper-id 2401.00001                        # 生成文章
smearglepaper review-article data/articles/2401.00001.md         # 审查文章
smearglepaper improve-article data/articles/2401.00001.json      # 优化文章
```

**③ 论文深度解读 Agent**

```bash
smearglepaper agent "解读这篇论文" --paper-url https://arxiv.org/abs/1706.03762
```

<div align="right"><a href="#smearglepaper">↑ 返回顶部</a></div>

---

## 📦 安装

> **前置条件**：Python 3.10+，推荐使用 Conda

**Conda（推荐）**

```bash
conda env create -p ./.conda/envs/smearglepaper -f environment.yml
conda run -p ./.conda/envs/smearglepaper python -m pip install -e ".[dev]"
```

<details>
<summary><strong>📋 Pip 安装方式 — 点击展开</strong></summary>
<br>

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

</details>

```bash
# 验证安装
smearglepaper preflight
```

<div align="right"><a href="#smearglepaper">↑ 返回顶部</a></div>

---

## 🛠️ CLI 命令

<table>
<tr><th>命令</th><th>说明</th></tr>
<tr><td><code>smearglepaper topics</code></td><td>列出可用主题预设</td></tr>
<tr><td><code>smearglepaper</code></td><td>启动 Agent 原生 TUI</td></tr>
<tr><td><code>smearglepaper run paper-to-article</code></td><td>运行可恢复的论文写作工作流</td></tr>
<tr><td><code>smearglepaper run paper-to-wechat</code></td><td>生成默认 dry-run、真实写入需审批的微信草稿</td></tr>
<tr><td><code>smearglepaper runs list</code></td><td>查看、恢复、重试或取消 Runtime 运行</td></tr>
<tr><td><code>smearglepaper collect-arxiv</code></td><td>收集 arXiv 论文</td></tr>
<tr><td><code>smearglepaper collect-blogs</code></td><td>收集 AI 博客</td></tr>
<tr><td><code>smearglepaper collect-github</code></td><td>收集 GitHub Trending</td></tr>
<tr><td><code>smearglepaper rank-papers</code></td><td>排序论文</td></tr>
<tr><td><code>smearglepaper ingest-paper</code></td><td>读取论文</td></tr>
<tr><td><code>smearglepaper generate-article</code></td><td>生成文章</td></tr>
<tr><td><code>smearglepaper review-article</code></td><td>审查文章质量</td></tr>
<tr><td><code>smearglepaper improve-article</code></td><td>优化文章</td></tr>
<tr><td><code>smearglepaper create-wechat-draft</code></td><td>兼容层：生成微信草稿</td></tr>
<tr><td><code>smearglepaper agent</code></td><td>以自然语言启动 Runtime 工作流</td></tr>
<tr><td><code>smearglepaper daily-digest</code></td><td>每日摘要</td></tr>
<tr><td><code>smearglepaper trend-analysis</code></td><td>趋势分析</td></tr>
</table>

**主题预设**：`latest_ai` · `agents` · `nlp` · `nlp_semantics` · `nlp_syntax` · `nlp_pragmatics`

<div align="right"><a href="#smearglepaper">↑ 返回顶部</a></div>

---

## 🔧 MCP 服务

SmearglePaper 可作为 MCP 服务器运行，与 AI Agent 无缝集成：

```bash
python -m mcp_server.server
```

<details>
<summary><strong>📋 可用 MCP 工具列表 — 点击展开</strong></summary>
<br>

| 工具 | 说明 |
|------|------|
| `collect_papers` | 收集论文 |
| `collect_blogs` | 收集博客 |
| `rank_latest` | 排序论文 |
| `read_paper` | 读取论文 |
| `write_article` | 生成文章 |
| `review_article` | 审查文章 |
| `improve_article` | 优化文章 |
| `create_draft` | 创建微信草稿 |
| `publish_article` | 发布文章 |

详见 [docs/MCP.md](docs/MCP.md)。

</details>

<div align="right"><a href="#smearglepaper">↑ 返回顶部</a></div>

---

## ⚙️ 配置

```bash
cp .env.example .env
```

编辑 `.env`：

```bash
# LLM 配置（必填）
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=deepseek-chat

# 微信公众号配置（可选，仅发布功能需要）
WECHAT_APP_ID=your-app-id
WECHAT_APP_SECRET=your-app-secret
```

> 💡 未配置 LLM 时，SmearglePaper 使用本地模板兜底，仍可离线测试完整工作流。

<div align="right"><a href="#smearglepaper">↑ 返回顶部</a></div>

---

## 📁 项目结构

```
SmearglePaper/
├── src/
│   ├── smearglepaper/      # 核心业务逻辑
│   └── mcp_server/         # MCP 服务器
├── data/
│   ├── papers/             # 收集的论文
│   ├── articles/           # 生成的文章
│   ├── covers/             # 封面图
│   └── figures/            # 提取的图表
├── workspace/
│   ├── paper_writing/      # 论文写作工作区
│   └── tasks/              # 任务管理
├── skills/                 # 工作流技能
├── prompts/                # LLM 提示词
└── tests/                  # 测试
```

<div align="right"><a href="#smearglepaper">↑ 返回顶部</a></div>

---

## 🧪 开发

<details>
<summary><strong>开发环境、测试与调试 — 点击展开</strong></summary>
<br>

```bash
# 运行测试
python -m pytest -q

# 检查环境
python -m smearglepaper preflight

# 测试 MCP 导入
python -c 'import mcp_server.server; print("mcp import ok")'
```

</details>

<div align="right"><a href="#smearglepaper">↑ 返回顶部</a></div>

---

## 🤝 贡献

欢迎所有形式的贡献！

1. Fork 本仓库
2. 创建特性分支（`git checkout -b feature/amazing-feature`）
3. 提交更改（`git commit -m 'feat: add amazing feature'`）
4. 推送到分支（`git push origin feature/amazing-feature`）
5. 创建 Pull Request

<div align="right"><a href="#smearglepaper">↑ 返回顶部</a></div>

---

## 📄 许可证

MIT License — 详见 [LICENSE](LICENSE)

---

## 🙏 致谢

- [arXiv](https://arxiv.org/) — 论文来源
- [DeepSeek](https://api.deepseek.com/) — LLM API
- [WeChat Official Account API](https://developers.weixin.qq.com/) — 微信公众号接口

---

<p align="center">
  <strong>⭐ 如果这个项目对你有帮助，请给个 Star 支持一下！</strong>
</p>

<p align="center">
  <a href="https://star-history.com/#GetIT-Sunday/SmearglePaper&Date">
    <img src="https://api.star-history.com/svg?repos=GetIT-Sunday/SmearglePaper&type=Date" alt="Star History Chart" width="600">
  </a>
</p>

<p align="center">
  <sub>Made with ✨ by <a href="https://github.com/GetIT-Sunday">GetIT-Sunday</a> using <a href="https://github.com/GetIT-Sunday/ReadmeMagic-github-readme-design-skill">ReadmeMagic</a></sub>
</p>
