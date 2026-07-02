<p align="center">
  <h1 align="center">🎨 SmearglePaper</h1>
  <p align="center">
    <strong>AI 论文自动化写作与发布助手</strong>
  </p>
  <p align="center">
    <a href="#-features">功能</a> • 
    <a href="#-install">安装</a> • 
    <a href="#-quick-start">快速开始</a> • 
    <a href="#-cli-commands">CLI 命令</a> • 
    <a href="#-mcp-server">MCP 服务</a>
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.1.0-blue?style=flat-square" alt="Version">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-yellow?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/arXiv-papers-orange?style=flat-square" alt="arXiv">
  <img src="https://img.shields.io/badge/WeChat-公众号-blue?style=flat-square" alt="WeChat">
  <img src="https://img.shields.io/github/stars/GetIT-Sunday/SmearglePaper?style=social" alt="Stars">
</p>

---

## ✨ 功能特性

SmearglePaper 将最新的 AI 论文转化为结构化的中文文章草稿。灵感来自宝可梦"图图图犬"（Smeargle），该项目将论文"绘制"成可读的技术文章。

<table>
  <tr>
    <td width="50%">
      <h3>📄 论文收集</h3>
      <ul>
        <li>从 arXiv 收集最新论文</li>
        <li>从 AI 工程 RSS/Atom 订阅源收集博客</li>
        <li>按主题相关性、新鲜度和 AI 类别信号排序</li>
      </ul>
    </td>
    <td width="50%">
      <h3>📝 文章生成</h3>
      <ul>
        <li>使用 PyMuPDF 下载解析 PDF</li>
        <li>提取图表候选</li>
        <li>用 LLM 生成中文深度解读文章</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>🔍 质量审查</h3>
      <ul>
        <li>文章质量评分</li>
        <li>LLM 编辑优化</li>
        <li>本地模板兜底</li>
      </ul>
    </td>
    <td width="50%">
      <h3>📱 微信发布</h3>
      <ul>
        <li>生成微信公众号兼容 HTML</li>
        <li>创建封面图</li>
        <li>创建/更新微信草稿</li>
      </ul>
    </td>
  </tr>
</table>

---

## 🚀 快速开始

### 一键运行完整流水线

```bash
smearglepaper agent-run --topic agents --days 30 --top-k 5
```

### 分步运行

```bash
# 1. 检查环境
smearglepaper preflight

# 2. 收集论文
smearglepaper collect --topic agents --days 30 --max-results 50

# 3. 排序论文
smearglepaper rank --top-k 5

# 4. 生成文章
smearglepaper write --paper-id 2401.00001

# 5. 审查文章
smearglepaper review-article data/articles/2401.00001.md

# 6. 优化文章
smearglepaper improve-article data/articles/2401.00001.json
```

### 论文深度解读 Agent

```bash
smearglepaper agent "解读这篇论文" --paper-url https://arxiv.org/abs/1706.03762
```

---

## 📦 安装

### Conda（推荐）

```bash
# 创建环境
conda env create -p ./.conda/envs/smearglepaper -f environment.yml

# 安装包
conda run -p ./.conda/envs/smearglepaper python -m pip install -e ".[dev]"
```

### Pip

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

### 验证安装

```bash
smearglepaper preflight
```

---

## 🛠️ CLI 命令

| 命令 | 说明 |
|------|------|
| `smearglepaper topics` | 列出可用主题预设 |
| `smearglepaper collect` | 收集 arXiv 论文 |
| `smearglepaper collect-blogs` | 收集 AI 博客 |
| `smearglepaper collect-github` | 收集 GitHub Trending |
| `smearglepaper rank` | 排序论文 |
| `smearglepaper read` | 读取论文 |
| `smearglepaper write` | 生成文章 |
| `smearglepaper review-article` | 审查文章 |
| `smearglepaper improve-article` | 优化文章 |
| `smearglepaper draft` | 生成草稿 |
| `smearglepaper agent-run` | 运行完整流水线 |
| `smearglepaper daily-digest` | 每日摘要 |
| `smearglepaper trend-analysis` | 趋势分析 |

### 主题预设

- `latest_ai` — cs.AI, cs.CL, cs.LG, cs.CV
- `agents` — LLM Agent, 多 Agent, 工具使用
- `nlp` — cs.CL
- `nlp_semantics`, `nlp_syntax`, `nlp_pragmatics`

---

## 🔧 MCP 服务

SmearglePaper 可作为 MCP 服务器运行，支持与 AI Agent 集成。

```bash
python -m mcp_server.server
```

### 可用 MCP 工具

- `collect_papers` — 收集论文
- `collect_blogs` — 收集博客
- `rank_latest` — 排序论文
- `read_paper` — 读取论文
- `write_article` — 生成文章
- `review_article` — 审查文章
- `improve_article` — 优化文章
- `create_draft` — 创建微信草稿
- `publish_article` — 发布文章

详见 [docs/MCP.md](docs/MCP.md)。

---

## ⚙️ 配置

复制 `.env.example` 到 `.env`：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```bash
# LLM 配置
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=deepseek-chat

# 微信公众号配置（可选）
WECHAT_APP_ID=your-app-id
WECHAT_APP_SECRET=your-app-secret
```

> 💡 如果未配置 LLM，SmearglePaper 会使用本地模板，仍可离线测试工作流。

---

## 📁 项目结构

```
SmearglePaper/
├── src/
│   ├── smearglepaper/      # 核心代码
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

---

## 🧪 开发

```bash
# 运行测试
python -m pytest -q

# 检查环境
python -m smearglepaper preflight

# 测试 MCP 导入
python -c 'import mcp_server.server; print("mcp import ok")'
```

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

---

## 📄 License

MIT License - 详见 [LICENSE](LICENSE)

---

<p align="center">
  <strong>⭐ 如果这个项目对你有帮助，请给个 Star 支持一下！</strong>
</p>

<p align="center">
  <a href="https://star-history.com/#GetIT-Sunday/SmearglePaper&Date">
    <img src="https://api.star-history.com/svg?repos=GetIT-Sunday/SmearglePaper&type=Date" alt="Star History Chart" width="600">
  </a>
</p>
