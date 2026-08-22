# SOP 多版本局域网部署

本目录保留本机 2026-08-19 至 2026-08-22 的代表性网页版本入口。每个服务使用独立源目录和端口，不复制或覆盖生产运行数据。

| 端口 | 版本 | systemd 用户服务 |
| --- | --- | --- |
| 8096 | 当前对话完整集成版 | `sop-platform.service` |
| 8099 | 版本入口页 | `sop-version-index.service` |
| 8101 | 2026-08-22 视频库流程变体 | `sop-version-library.service` |
| 8102 | 2026-08-21 自动跟踪恢复版 | `sop-version-tracking.service` |
| 8103 | 2026-08-20 协同标注版 | `sop-version-collaborative.service` |
| 8104 | 2026-08-19 网页前后端/公网展示版 | `sop-version-web-public.service` |
| 8105 | 2026-08-19 第三摄像头版 | `sop-version-third-camera.service` |
| 8081 | CVAT 2.73.1 | Docker Compose |

安装和启动历史服务：

```bash
chmod +x deploy/versioned/*.sh
deploy/versioned/install_user_services.sh
deploy/versioned/verify_versions.sh 192.168.1.129
```

当前机器只有一个局域网 IPv4 `192.168.1.129`，所以用独立端口区分版本。不要为此给无线网卡随意添加未分配的 IP，否则可能与现场设备冲突。

私有配置保存在 `/home/xjai/.config/sop-platform/`，权限为 `600`，不提交到 Git。历史服务不得共用同一个可写 SQLite；本次部署继续使用各自原目录的数据，主服务数据库已在账号恢复前备份。
