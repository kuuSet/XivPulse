# 公网服务器部署

服务器形态面向“一台服务器、一名管理员、一个研究画像”。它不提供多账号、租户隔离或团队权限。公网只发布 Caddy 的 `443`，应用 `8765` 只在 Docker 内网可见；不支持公网明文 HTTP。

## 1. 准备配置

服务器需安装 Docker Engine 与 Compose v2，并允许公网访问 TCP/UDP `443`。把示例复制为实际配置：

```bash
cp .env.server.example .env.server
```

生成 Argon2 密码哈希和随机会话密钥：

```bash
python -m pip install argon2-cffi
python scripts/generate_server_secrets.py
```

脚本会交互读取密码，只输出以下两行，不保存明文密码。把输出写入 `.env.server`，Argon2 哈希必须保留单引号，防止 Compose 解释其中的 `$`：

```dotenv
XIVPULSE_AUTH_PASSWORD_HASH='$argon2id$...'
XIVPULSE_AUTH_SESSION_SECRET=...
```

再设置服务器真实公网 IP：

```dotenv
XIVPULSE_PUBLIC_HOST=203.0.113.10
XIVPULSE_PUBLIC_URL=https://203.0.113.10
XIVPULSE_SERVER_ALLOWED_HOSTS=203.0.113.10,127.0.0.1,localhost,xivpulse
```

`XIVPULSE_AUTH_SESSION_SECRET` 至少 32 字节，管理员密码只以 Argon2 哈希保存。SMTP 和模型 API 可通过同一文件注入；SMTP 环境变量优先于 SQLite 设置，浏览器接口只返回授权码“是否已配置”。

## 2. 首次启动

```bash
docker compose -f docker-compose.server.yml up -d --build
docker compose -f docker-compose.server.yml ps
docker compose -f docker-compose.server.yml logs -f xivpulse caddy
```

首次使用语义召回时会下载量化的 `BAAI/bge-small-en-v1.5`。模型与数据库统一保存到 `xivpulse-data` 卷，重建容器不会重复下载。打开 `https://公网IP`；在客户端信任 Caddy 根证书之前，浏览器会显示证书不受信任，这是内部 CA 的预期行为。

## 3. 信任自签名根证书

从持久卷导出根证书：

```bash
docker compose -f docker-compose.server.yml cp \
  caddy:/data/caddy/pki/authorities/local/root.crt ./xivpulse-root.crt
```

Windows 管理员终端：

```powershell
certutil -addstore -f Root .\xivpulse-root.crt
```

macOS：

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain xivpulse-root.crt
```

Debian/Ubuntu：

```bash
sudo cp xivpulse-root.crt /usr/local/share/ca-certificates/xivpulse.crt
sudo update-ca-certificates
```

只把根证书安装到你控制的客户端。不要公开分发 Caddy 的 CA 私钥，也不要复制 `/data/caddy/pki/authorities/local/root.key`。

## 4. SMTP 日报

推荐使用邮件服务商提供的授权码，而不是账户登录密码。默认是 `STARTTLS + 587`；需要隐式 TLS 时使用 `ssl + 465`。在网页“系统设置 → 日报邮件”保存后先发送测试邮件。完整日更只有在日报快照保存完成后才自动发送；初筛失败会在主题与正文标记“不完整”。邮件失败不会回滚论文分析或日报，历史日报可在日报页手动重发。

## 5. 备份、升级与恢复

先暂停容器，再从数据卷复制 SQLite 文件：

```bash
docker compose -f docker-compose.server.yml stop
docker compose -f docker-compose.server.yml cp \
  xivpulse:/app/data/xivpulse.db ./backup-xivpulse.db
docker compose -f docker-compose.server.yml start
```

升级前同时备份 `.env.server`、数据库和 Caddy 数据卷。升级应用：

```bash
docker compose -f docker-compose.server.yml pull
docker compose -f docker-compose.server.yml up -d --build
```

不要执行 `docker compose ... down -v`，除非明确要删除论文、分析、配置、日报、模型缓存和 Caddy CA。普通 `down`、重建和镜像升级不会删除命名卷。

## 6. 故障排查

```bash
docker compose -f docker-compose.server.yml ps
docker compose -f docker-compose.server.yml logs --tail=200 xivpulse
docker compose -f docker-compose.server.yml logs --tail=200 scheduler
docker compose -f docker-compose.server.yml logs --tail=200 caddy
```

- 应用启动失败并提示凭据：检查管理员用户名、单引号包裹的 Argon2 哈希和至少 32 字节会话密钥。
- 浏览器仍提示证书：确认访问的是 `.env.server` 中的同一个 IP，并重新导入当前 Caddy 数据卷导出的根证书。
- 登录后仍返回 401：确认始终使用 HTTPS；服务器模式的会话 Cookie 强制 `Secure`，通过 HTTP 不会发送。
- 邮件中的链接错误：把 `XIVPULSE_PUBLIC_URL` 设为客户端实际访问的 `https://公网IP`。
- 模型反复下载：确认应用和调度器都挂载同一个 `xivpulse-data` 卷。
- 模型下载源不可达：任务会明确标记语义降级，并只保留关注作者召回，不会中断整条日更；恢复外网后在设置页执行“下载并预热”。
