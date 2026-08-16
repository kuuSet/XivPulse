# TODO

- [ ] 支持本机 Codex CLI 调用
  - 通过独立的 `backend/app/integrations/codex/client.py` 调用 `codex exec`，不读取或解析 `~/.codex/auth.json`。
  - 实现前确定 Docker 接入方式：容器内安装 Codex CLI 并只读挂载登录目录，或由宿主机提供桥接服务。
  - 最小实现只处理单次调用、超时和错误返回，不增加队列、重试或额外抽象。
  - 验收：Docker 开发环境可以完成一次真实调用，且日志和接口响应不泄露凭据。
