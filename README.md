# XivPulse

<p align="center">
  <img src="frontend/public/xivpulse-logo.png" alt="XivPulse" width="680">
</p>

<p align="center">
  面向研究者的自托管 arXiv 情报与阅读工作台<br>
  Self-hosted arXiv discovery, analysis, recommendation, and research reporting.
</p>

<p align="center">
  <a href="https://github.com/kuuSet/XivPulse/actions/workflows/ci.yml"><img src="https://github.com/kuuSet/XivPulse/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-1769aa" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/Python-3.12+-078dcc" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ed" alt="Docker Compose">
</p>

XivPulse 会根据你维护的关注标签持续获取 arXiv 新论文，再使用 OpenAI 兼容模型依次完成批量标题预筛、摘要相关度评分和高分论文全文深读。系统还提供阅读工作流、分类收藏、研究趋势、路线图、每日研究报告与邮件投递，所有数据默认保存在你自己的设备或服务器中。

> [!IMPORTANT]
> XivPulse 是独立开源项目，与 arXiv 或 Cornell University 无隶属或背书关系。模型生成的摘要和评价可能出错，重要结论应回到论文原文核验。

## 为什么使用 XivPulse

- **高召回标题预筛**：LLM 批量读取标题、arXiv 分类和标签描述，只排除明显无关论文，降低后续摘要评分成本。
- **线性三级流水线**：标题预筛、摘要评分、全文深读依次执行，每一级完成后立即保存；Provider 失败会直接中止任务。
- **分层阅读决策**：先看一句话结论、方向匹配和多维评分，再按需查看方法、证据、贡献、局限及支持/质疑视角。
- **可学习的个性化推荐**：喜欢、不喜欢、收藏、忽略、评分和结构化原因会影响后续排序，并保留可解释的推荐理由。
- **研究管理闭环**：用待读、阅读中、已读、待复现、已引用和归档状态维护阅读队列，通过收藏库和自定义分类库组织课题。
- **趋势与路线**：仅使用受控词表中的正式研究方向生成日变化、周变化、方向共现和研究路线，避免把 arXiv 分类或自由长句误当成趋势标签。
- **每日研究报告**：汇总研究态势、高分论文、共同方法、证据形态、方向变化和推荐阅读队列，并可选调用 AI 生成跨论文长篇综合研判。
- **成本可观察**：提供经济、平衡、深度三档分析预算，保存 Provider 实际返回的输入、缓存输入、输出、推理及总 Token。
- **自托管与跨平台**：支持 Windows 本地启动，以及 Windows、macOS、Linux 上的 Docker Compose；另提供带登录和 HTTPS 的服务器部署形态。

## 工作流程

```text
arXiv 每日更新
      |
      v
批量标题 + 分类 LLM 预筛
      |
      v
relevant / uncertain -> 逐篇摘要 LLM 评分
      |                               |
      v                               v
高优先级论文全文深读             趋势与研究路线
      |                               |
      +-------------> 推荐排序 <------+
                         |
                         v
             研究日报、阅读队列与邮件
```

标题预筛、摘要评分、全文深读和可选的 AI 日报综合研判都会调用你配置的模型服务，并按实际处理量产生费用。标题预筛不发送摘要，是三级流水线中最便宜的一层。

## 快速开始

### Docker Compose，推荐

Docker 方式适用于 Windows、macOS 和 Linux，宿主机不需要单独安装 Python 或 Node.js。

```bash
git clone https://github.com/kuuSet/XivPulse.git
cd XivPulse
docker compose up -d --build
```

打开 <http://127.0.0.1:8765>。查看运行状态：

```bash
docker compose ps
docker compose logs -f xivpulse
```

论文、设置、分析和日报统一保存在 `xivpulse-data` 卷；重新构建容器不会清空它们。

停止服务但保留数据：

```bash
docker compose down
```

> [!WARNING]
> `docker compose down -v` 会删除数据卷，包括论文、分析、设置和日报。执行前请先备份。

### Windows 本地运行

要求 Python 3.12+ 和 Node.js 20+。克隆仓库后运行：

```powershell
git clone https://github.com/kuuSet/XivPulse.git
Set-Location XivPulse
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

脚本会创建虚拟环境、安装依赖、构建前端并打开浏览器。完成首次安装后，也可以直接双击 `打开 XivPulse.cmd`；停止服务时双击 `停止 XivPulse.cmd`。

后续快速启动：

```powershell
.\start.ps1 -SkipInstall -SkipFrontend
```

## 配置模型服务

XivPulse 支持两种模型连接。在网页“系统设置”中选择后先测试连接：

- `API`：填写 OpenAI 兼容接口的 Base URL、模型名称和 API Key，适合稳定的无人值守任务。
- `本机 Codex`：复用运行 XivPulse 的机器上已经完成 `codex login` 的 Codex CLI。每次分析都会启动独立 Codex 进程，因此比直接 API 更慢、代理上下文开销也更高。

未配置可用的模型连接时仍可浏览界面和使用本地确定性功能，但不会产生真实的 AI 分析。

Docker 用户也可以复制示例配置：

```bash
cp .env.example .env
```

然后只在本机 `.env` 中填写：

```dotenv
XIVPULSE_API_BASE_URL=https://api.openai.com/v1
XIVPULSE_MODEL_NAME=your-model
XIVPULSE_API_KEY=your-key
```

Docker 中使用本机 Codex 时，在 `.env` 中填写宿主机 Codex 登录目录，然后显式叠加可选配置：

```dotenv
XIVPULSE_CODEX_HOME=C:/Users/you/.codex
```

```powershell
docker compose -f docker-compose.yml -f docker-compose.codex.yml up -d --build
```

该配置只读挂载 `auth.json`，XivPulse 不读取或解析其中内容，也不会把它保存到数据库。Codex 在空工作目录和 `read-only` 沙箱中处理论文数据。认证方式与非交互模式分别见 OpenAI 官方的 [Codex authentication](https://learn.chatgpt.com/docs/auth.md) 和 [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode.md)。

`.env`、SQLite 数据库、日志和证书默认不会进入 Git 或 Docker 构建上下文。设置接口只返回密钥是否已配置，不会把 API Key 或 Codex 登录凭据返回浏览器。

## 首次设置建议

1. 在“关注设置”中从候选目录选择或创建 3-12 个边界清晰的长期研究方向。
2. 在“系统设置”中选择分析预算、标题预筛批量大小和全文深读门槛。
3. 配置模型连接，并先用少量候选测试输出质量、速度和 Token 消耗。
4. 运行一次完整日更，检查候选量、初筛完成度、正式方向和推荐理由。
5. 持续使用喜欢、不喜欢、收藏、忽略和结构化原因反馈，让排序逐步贴近你的判断。

推荐系统的评分、反馈传播和阈值说明见 [RECOMMENDER.md](RECOMMENDER.md)，完整界面与工作流说明见 [USER_GUIDE.md](USER_GUIDE.md)。

## 每日自动运行

Docker Compose 已包含调度器，会读取网页中保存的计划时间和时区，每天最多启动一次完整流水线，并在容器错过计划时间后补跑。

Windows 本地模式可以安装计划任务，默认每天 13:30 运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_task.ps1
```

自定义时间或手动运行：

```powershell
.\install_task.ps1 -At "08:30"
.\run_daily.ps1
```

计划时间电脑关机时不会凭空分析；Windows 会在电脑恢复且条件允许后补跑。网页服务也必须由启动器、计划任务或 Docker 容器实际运行。

## 服务器部署

仓库提供单用户服务器 Compose：应用和调度器仅在 Docker 内网通信，由 Caddy 对外发布 HTTPS，并强制管理员登录。它适合把自己的 XivPulse 实例部署到服务器后从其他设备访问。

服务器模式需要配置：

- 公网 IP 或主机名与可信 Host
- Argon2 管理员密码哈希
- 至少 32 字节的会话密钥
- Caddy HTTPS 证书与客户端信任
- 可选的 SMTP 日报邮件参数

不要把本地 Compose 的 `8765` 端口直接暴露到公网。完整步骤、证书信任、备份、升级和故障排查见 [SERVER_DEPLOYMENT.md](SERVER_DEPLOYMENT.md)。当前服务器形态是单用户实例；多用户应部署相互隔离的实例。

## 数据与隐私

本地源码运行默认使用 `data/xivpulse.db`。

SQLite 中保存论文元数据、摘要、模型分析、Token 记录、反馈、阅读状态、分类库、设置和每日研究报告。应用不会长期保存论文 PDF 或 HTML 全文。当前版本不会自动清理历史数据，数据会持续积累，直到用户自行备份或删除数据库。

使用第三方模型 Provider 时，论文标题、摘要以及为深读提取的正文可能会发送给该服务。请只配置可信的 HTTPS 地址，并了解服务商的数据保留政策。安全问题请遵循 [SECURITY.md](SECURITY.md) 私下报告，不要在公开 Issue 中粘贴凭据、Cookie、数据库或敏感日志。

## 项目文档

| 文档 | 内容 |
| --- | --- |
| [USER_GUIDE.md](USER_GUIDE.md) | 页面、标签体系、阅读工作流、日报、提示词和存储说明 |
| [RECOMMENDER.md](RECOMMENDER.md) | 推荐评分、反馈影响、初筛与深读阈值 |
| [SERVER_DEPLOYMENT.md](SERVER_DEPLOYMENT.md) | 公网服务器、登录、自签名 HTTPS、SMTP、备份与升级 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 本地开发、测试要求和贡献规范 |
| [SECURITY.md](SECURITY.md) | 安全支持范围和漏洞报告方式 |

后端启动后可通过 <http://127.0.0.1:8765/docs> 查看交互式 API 文档。

## 开发

后端使用 FastAPI、SQLAlchemy 和 SQLite，前端使用 React、TypeScript 和 Vite。论文筛选通过配置的 OpenAI 兼容模型执行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt ruff
Set-Location frontend
npm ci
```

提交前运行：

```powershell
.\.venv\Scripts\python.exe -m ruff check backend\app backend\tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\check_public_tree.py
Set-Location frontend
npm run build
```

运行 Chromium 无头界面测试：

```powershell
docker compose --profile e2e run --rm --build e2e
```

首次运行会下载独立的 Playwright 开发镜像；浏览器不会进入 XivPulse 生产镜像。

CI 会在每次 Push 和 Pull Request 中执行敏感信息扫描、Ruff、后端测试、前端生产构建和 Docker 镜像构建。欢迎通过 Issue 描述使用场景和问题，也欢迎提交范围清晰、包含验证方式的 Pull Request。

## License

XivPulse 使用 [MIT License](LICENSE) 发布。
