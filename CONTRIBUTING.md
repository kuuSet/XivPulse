# Contributing to XivPulse

感谢你改进 XivPulse。提交变更前，请先创建一个内容聚焦的 Issue 或 Pull Request，并说明用户场景、行为变化和验证方式。

## 本地开发

后端要求 Python 3.12，前端要求 Node.js 22。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt ruff
Set-Location frontend
npm ci
```

Linux 和 macOS 使用对应的 `.venv/bin/python` 路径。

## 提交前检查

```powershell
.\.venv\Scripts\python.exe -m ruff check backend\app backend\tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\check_public_tree.py
Set-Location frontend
npm run build
```

涉及 Docker 配置时，还应执行：

```powershell
docker compose config --quiet
docker build -t xivpulse:test .
```

## 数据与密钥

- 不要提交 `.env`、`.env.server`、数据库、日志、模型缓存、证书、截图或备份。
- 测试只能使用明显无效的占位密钥，不要复制真实 API Key、SMTP 授权码、Cookie 或 Argon2 管理员哈希。
- 新增配置时，API 响应只能返回“是否已配置”，不能把密钥原文返回浏览器。
- 新增研究标签时必须服从受控词表边界，不要把 arXiv 分类、模型名、数据集名或自由长句混入正式研究方向。

## 变更范围

尽量保持迁移向后兼容，不删除用户的论文、分析、日报、反馈和阅读状态。数据库结构变更需要包含迁移逻辑和测试；涉及推荐、方向归一化或日报聚合时，应补充确定性测试。
