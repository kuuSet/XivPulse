# Security Policy

## Supported Version

当前只维护默认分支的最新版本。服务器部署应始终启用 HTTPS、单用户认证和安全 Cookie，并定期备份 SQLite 数据库与 Caddy CA。

## Reporting a Vulnerability

请优先使用 GitHub 仓库的 Private vulnerability reporting 功能提交漏洞。不要在公开 Issue 中发布可用的凭据、会话 Cookie、数据库、日志原文或可直接利用的攻击细节。

报告中请包含受影响版本、部署模式、复现步骤、预期影响和建议缓解方式。维护者确认问题前，请避免对不属于你的公网实例进行测试。

## Secret Handling

XivPulse 不会通过设置 API 返回模型 API Key 或 SMTP 授权码。`.env*`、数据库、证书和运行日志默认不进入 Git 或 Docker 构建上下文。
