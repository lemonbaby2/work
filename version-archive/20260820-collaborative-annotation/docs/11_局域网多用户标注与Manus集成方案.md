# 局域网多用户标注与 Manus 集成方案

## 1. 已实现能力

- `SQLite WAL` 保存用户、登录会话、标注版本、关键帧编辑锁和审计日志。
- 角色：`admin`、`reviewer`、`annotator`、`viewer`。
- 同一帧编辑锁为 15 分钟；重复保存使用版本号检查，冲突时要求刷新。
- 标签体系按三条产线独立保存，支持中文逗号、英文逗号或换行输入。
- 人工只画关键帧；可按 1/5/15/30 帧步长生成中间帧插值候选。
- 插值结果始终标记为 `pending` 和 `auto_generated`，人工复核前不进入正式真值。
- 数据库默认路径：`runtime/collaboration.sqlite3`，生产环境建议放到 `/var/lib/sop/` 并每日备份。

## 2. Windows 访问方式

同一局域网不需要 SSH。服务器运行：

```bash
cd deploy/lan-collaboration
SOP_DEFAULT_PASSWORD='首次强密码' CVAT_URL='http://192.168.1.129:8081' ./start-lan.sh
```

启动脚本优先使用 DGX Spark 已验证的 `/home/xjai/micromamba/envs/sop/bin/python3.12`；其他服务器可设置 `SOP_PYTHON=/path/to/venv/bin/python`。

Windows 浏览器直接打开：

```text
http://192.168.1.129:8097/#annotation
```

只在网络策略禁止直连 8097 时使用 SSH 隧道：

```powershell
ssh -L 8097:127.0.0.1:8097 xjai@192.168.1.129
```

随后浏览器打开 `http://127.0.0.1:8097/#annotation`。每个标注员必须使用独立账号，不能共享管理员账号。

## 3. 高效率标注流程

1. 先按产品、相机和工序切分任务，冻结三条产线各自的标签字典。
2. 用 YOLOE/YOLO-World 生成预标注，人工只修正框、类别、遮挡和漏检。
3. 稳定固定工位按 15–30 帧抽样；手、工具和快速运动目标按 3–5 帧；遮挡和工序切换处逐帧。
4. 同一目标设置起止关键帧，生成中间帧候选。方向、尺度或遮挡明显变化时增加关键帧。
5. 质量员抽检所有 NG、所有稀有类、所有工序边界，并随机抽检正常样本。
6. 训练集按产品 SN/视频整段划分，不能把相邻帧随机拆到训练集和测试集，否则精度会虚高。
7. 只导出 `human_confirmed`，插值候选和预标注不自动升级为真值。

## 4. Manus 集成边界

Manus 页面是 HTTPS 公网域名，不能安全地直接 iframe 内网 `http://192.168.1.129:8097`：浏览器会拦截混合内容，公网服务也无法连接厂内私网。推荐方式：

- Manus 的“数据标注”导航跳转到厂内 HTTPS 域名 `https://sop.factory.example.com/#annotation`。
- Nginx 在厂内或 VPN 网关终止 HTTPS，再代理到 `127.0.0.1:8097` 和 CVAT `127.0.0.1:8081`。
- 使用企业 VPN、Tailscale/Headscale 或零信任网关限制访问人员；不要把摄像头和 SQLite 直接暴露公网。
- Manus 只作为统一入口和管理总览，视频、标注、模型与 MES 数据保留在厂内。

仓库已提供 `deploy/lan-collaboration/nginx-sop.conf` 和 `manus-integration.json`。真正更新已发布 Manus 页面仍需要该 Manus 项目的源码仓库或项目部署权限。

## 5. 上线前要求

- 首次启动必须设置 `SOP_DEFAULT_PASSWORD`，登录后再创建独立账号并删除/停用默认账号。
- Windows、DGX Spark 和 CVAT 使用 NTP 对时。
- 每日备份 SQLite 数据库、`runtime/*.jsonl`、CVAT 数据卷和证据图片。
- 防火墙仅允许厂内 VLAN/VPN 访问 443；8097、8081 不对公网开放。
