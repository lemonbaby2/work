# SOP 平台公网 HTTPS 部署

这一套配置用于“云主机或有公网入口的厂内服务器 + 固定域名”。不要把 Python 进程的 `8096` 端口直接映射到公网。

## 前置条件

- 一台可由管理员控制的 Linux 服务器；
- 解析到该服务器的域名，例如 `sop.example.com`；
- 公网只放行 TCP `80/443`，`8096` 仅绑定 `127.0.0.1`；
- 生产数据库、视频和标注图由授权管理员单独恢复，不从 GitHub 公开包中获取。

## 安装

1. 将项目放在 `/opt/sop-platform`，创建只能读写该目录的 `sop` 系统用户。
2. 将 `sop-platform.service.example` 复制为 `/etc/systemd/system/sop-platform.service`，按实际 Python 路径修改 `ExecStart`。
3. 将 `Caddyfile.example` 复制到 `/etc/caddy/Caddyfile`，把 `sop.example.com` 替换为真实域名。
4. 启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sop-platform
sudo systemctl reload caddy
curl https://sop.example.com/api/health
```

## 发布检查

- 浏览器必须显示有效 HTTPS 证书；
- 登录响应的 `sop_session` Cookie 必须带 `HttpOnly` 、`SameSite=Strict` 和 `Secure`；
- 未登录请求 `/media/*` 必须返回 401；
- 不得在仓库、Caddyfile 或截图中写入平台密码、CVAT Token 或相机凭据；
- 正式放行前应在身份提供商、VPN 或 Cloudflare Access 层再加一层企业身份验证。

## 临时公网验收

没有固定域名时，可用 Quick Tunnel 临时验收。它只暴露绑定在
`127.0.0.1:8097` 的第二个 SOP 进程，不暴露 CVAT `8081`，也不改变局域网
`8096` 服务：

```bash
chmod +x deploy/public/start_quick_tunnel.sh
./deploy/public/start_quick_tunnel.sh
```

脚本优先使用已安装的 Cloudflare Quick Tunnel；未安装完整 `cloudflared` 时自动
回退到 SSH HTTPS Tunnel。终端输出的 `https://*.trycloudflare.com` 或
`https://*.lhr.life` 是本次运行地址。关闭脚本后地址失效；
它没有 Cloudflare Access 企业身份层，只可用于短期验收，正式生产仍使用固定域名、
Caddy 或命名 Tunnel，并在外层增加企业身份验证。
