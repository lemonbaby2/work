# DGX 多摄像头、Wi-Fi 采购、Windows 低延迟与 SCP 交付说明

更新时间：2026-08-21（UTC）

## 1. 本次交付结论

当前采集主机已确认是 NVIDIA DGX Spark，GPU 为 NVIDIA GB10，主机局域网地址为 `192.168.1.129`。已枚举三台 USB/UVC 主视频设备，并集成到同一套 DGX 目标检测服务：

| 槽位 | 设备 | 稳定输入 | 接入方式 |
|---|---|---|---|
| 0 | Insta360 Link 2C | `http://127.0.0.1:18080/stream` | 复用现有 ustreamer，避免两个进程抢占 `/dev/video0` |
| 1 | icSpring WebCamera | `/dev/v4l/by-id/usb-icSpring_WebCamera_20240603201703-video-index0` | UVC 直连 |
| 2 | Jieli USB Camera | `/dev/v4l/by-id/usb-Jieli_Technology_USB_Composite_Device-video-index0` | UVC 直连 |

三路均使用 `/home/xjai/sop-model-store/yolo11n.pt`、CUDA 0 和容量 1 的最新帧队列。采集目标 15 FPS，推理目标 5 FPS；GPU 推理串行限流，过期帧丢弃，避免排队造成延迟不断累积。

网页地址：`http://192.168.1.129:8096`

CVAT 地址：`http://192.168.1.129:8081`

服务管理：

```bash
systemctl --user status sop-platform.service
systemctl --user restart sop-platform.service
journalctl --user -u sop-platform.service -f
```

## 2. 网页功能

“现场监控”页面包含：

- 单路实时检测、相机切换、截图和录像；
- 三路摄像头墙，可统一启动/停止；
- 每路采集 FPS、显示 FPS、端到端延迟、推理耗时、目标数、丢帧数、重连次数；
- USB 身份、稳定设备路径、UVC 模式和主机网络信息；
- Wi-Fi/工业相机采购矩阵；
- RTSP 网络相机接入示例。

页面输出的自动检测框仍是候选证据。没有人工确认、工艺条件和 MES 回执时，量产状态保持 `HOLD`。

## 3. Wi-Fi 摄像头购买建议

### 3.1 量产首选

购买“工业 GigE 相机 + 5 GHz/Wi-Fi 6 工业网桥”，而不是把消费级 Wi-Fi 摄像头直接作为质量判定设备。

建议候选：Basler ace 2、海康机器人 MV-CA 系列；再根据视野选择 C/CS 镜头、刚性支架和无频闪光源。无线部分由工业网桥承担，相机侧仍使用 GigE Vision。预算约 `4000-12000 元/点位`，包含镜头、光源、支架和网桥。优势是曝光、增益、帧率、硬触发和固定 IP 可控，故障边界清晰。

验收要求：

1. 目标最小特征至少 8-12 像素，关键缺陷应留更大余量。
2. 现场光照下锁曝光、白平衡和焦距。
3. 连续运行 8 小时无断流，断网后 30 秒内恢复。
4. P95 端到端延迟满足工位节拍；质量触发场景优先有线。
5. 支持本地协议，不依赖厂商云服务。

### 3.2 低预算试点

可选 Reolink RLC-510WA。采购前必须确认目标销售区域和目标固件开放 RTSP/ONVIF，因为同一系列不同固件能力可能不同。预算约 `500-1000 元/点位`。适合环境总览、低速工序和概念验证，不建议承担硬实时质量放行。

### 3.3 国内渠道备选

可选择 TP-Link VIGI Wi-Fi 固定机型，或海康威视带 `-W` 后缀且明确支持 RTSP/ONVIF 的固定机型。预算约 `400-1200 元/点位`。合同中应写明：

- 支持 5 GHz Wi-Fi；
- 支持 RTSP 和 ONVIF，提供码流路径；
- H.264 编码，码率、分辨率、帧率和 GOP 可配置；
- 支持固定 IP、NTP、断线自动恢复；
- 不需要厂商云才能拉取本地码流。

不建议在固定 SOP 工位采用自动云台追踪。云台运动会改变标定后的 ROI 和像素尺度。

## 4. 网络相机接入

复制示例：

```bash
cp config/network_cameras.example.json config/network_cameras.json
```

然后填写真实 RTSP URL：

```json
[
  {
    "name": "PCB工位WiFi相机",
    "url": "rtsp://USERNAME:PASSWORD@192.168.20.31:554/STREAM_PATH"
  }
]
```

保存后重启服务或在网页点击“刷新设备”。生产密码不得写入公开 ZIP、Git 仓库或截图。建议相机进入独立 VLAN，只允许 DGX/IPC 访问 RTSP、ONVIF、NTP 和必要管理端口。

Wi-Fi 建议使用独立 5 GHz SSID、固定信道、关闭客户端隔离；每台相机控制在 4-8 Mbps，并按并发路数预留至少 2 倍带宽。2.4 GHz 只用于无法覆盖 5 GHz 的低码率辅助工位。

## 5. Windows 原生低延迟客户端

现有 `SOP平台.exe` 是 Windows WinForms 启动器。2026-08-21 的源码新增“打开 DGX 三路原生监控”：

- 不加载完整管理网页；
- 登录 DGX 平台后直接请求三路 `/api/camera/mjpeg`；
- 每路独立解析 JPEG、显示和重连；
- 关闭 Windows 窗口不停止 DGX 推理；
- 密码不落盘，只在当前进程内使用。
- 单独部署 EXE 时自动进入远程客户端模式，不要求 Windows 安装 Python。

源码：`windows/SopPlatformLauncher.cs`

构建脚本：`windows/build_exe.ps1`

在 Windows PowerShell 执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
& .\windows\build_exe.ps1
```

生成的 `SOP平台.exe` 仍保留本地后端启动功能，同时可作为 DGX 原生三路监控客户端。2026-08-21 已使用 Mono C# 编译器在 DGX 上生成架构无关的 .NET Framework 4.5 WinForms PE 文件，文件类型为 `PE32 executable (GUI) Intel 80386 Mono/.Net assembly`。该文件已通过编译和 PE 格式检查，不再是旧 EXE；由于 DGX 是 ARM64 Linux，Windows 界面、账号登录、三路持续播放和断线重连仍必须在目标 Windows 主机做最终运行验收。

原生客户端减少的是浏览器 DOM、完整页面模块请求和渲染开销。总延迟仍包含相机曝光、编码、Wi-Fi、DGX 解码、GPU 推理、JPEG 编码和网络传输。量产验收必须测 P50/P95/P99，不以单次观感代替测试。

## 6. SCP/SFTP 回传

已确认 Windows 主机 `192.168.1.128` 在线且 TCP 22 可达，目标用户历史记录为 `lzp`；当前 DGX 没有可用的免密私钥或已授权登录凭据，因此自动 SCP 会收到 `Permission denied`。`192.168.1.117` 当前不在线。

Windows 管理员先配置 OpenSSH Server 和公钥后，在 DGX 执行：

```bash
scp SOP平台_Windows原生监控_20260821.zip lzp@192.168.1.128:'D:/lzpsop20260821/'
scp SHA256SUMS.txt lzp@192.168.1.128:'D:/lzpsop20260821/'
```

推荐使用 SSH 公钥，不把 Windows 密码写入脚本、命令历史或仓库。传输后在 Windows 校验：

```powershell
Get-FileHash D:\lzpsop20260821\SOP平台_Windows原生监控_20260821.zip -Algorithm SHA256
```

## 7. 交付文件

- `server.py`：三路采集、推理、MJPEG、设备与采购建议 API；
- `web/index.html`、`web/app.js`、`web/styles.css`：网页摄像头墙和采购矩阵；
- `config/camera_recommendations.json`：网页采购数据；
- `config/network_cameras.example.json`：RTSP 接入样例；
- `windows/SopPlatformLauncher.cs`：Windows 原生三路客户端源码；
- `windows/build_exe.ps1`：Windows EXE 构建脚本；
- `SOP平台.exe`：已交叉编译的 Windows .NET Framework 4.5 WinForms 客户端；
- `qa/dgx_three_camera_runtime_report_20260821.json`：三路实机启动、帧流和性能复测记录；
- `qa/annotation_tracking_ui_report_20260821.json`：光流轨迹、编辑和跳帧性能复测记录；
- `SHA256SUMS.txt`：交付包内文件校验清单；
- `~/.config/systemd/user/sop-platform.service`：当前 DGX 三路自启动配置；
- 本文档：运行、采购、Windows 与 SCP 完整说明。

## 8. 本次可确认与不可确认的边界

可确认：DGX/GPU 身份、三台相机枚举、三路各自取帧、软件依赖、网页和服务源码、Windows 主机 192.168.1.128 的 22 端口可达。

仍需现场确认：每个摄像头对应的实际工位、ROI、安装高度、镜头焦距、补光、量产类别模型、Wi-Fi 干扰、Windows 新 EXE 实机运行、SCP 授权和最终延迟验收。
