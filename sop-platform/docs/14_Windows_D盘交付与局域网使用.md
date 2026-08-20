# Windows D盘交付与局域网使用

## 交付位置

Windows 主机 `192.168.1.117` 和 `192.168.1.128` 的交付目录：

```text
D:\soplzp0820_20260820
```

## 直接使用局域网网页

DGX 主机与 Windows 在同一个局域网时，在 Windows 浏览器打开：

```text
http://192.168.1.129:8096
```

进入“现场监控”后点击“启动全部”，可查看当前实际连接的全部摄像头视频流。

## 摄像头身份

当前 DGX 已检测三台 USB/UVC 主视频节点：

1. Insta360 Link 2C：`/dev/video0`
2. USB Composite Camera：`/dev/video2`
3. USB Webcam：`/dev/video5`

这些设备是 USB 摄像头，不是 RTSP/GigE 网络摄像头，所以没有独立 IP。它们的画面由 DGX 主机 `192.168.1.129` 统一通过 SOP 网页推送。`/dev/ttyACM0` 仅作为串口控制通道，不承载视频。

## 标注与数据下载

在“数据标注”页面：

- “下载坐标 CSV”：Excel 可直接打开的已确认标注坐标。
- “下载坐标 JSON”：结构化坐标和审核状态。
- “一键下载完整 ZIP”：坐标、审核记录、标注截图、五段原始视频和本地数据集。

在“训练与部署”页面：

- 数据集总清单和每个数据集都可下载 CSV。
- 可选择 YOLO26N、YOLOE-26S + SAHI、RT-DETRv2、D-FINE 和 Anomalib。
- 点击“开始训练并验证”后，当前页面显示流水线状态、延迟、FPS、置信度和 10 张性能图。

未完成人工冻结真值集验收时，页面会明确标记为“基线验证”，不代表量产精度。

## Windows 本机启动

完整应用已解压到：

```text
D:\soplzp0820_20260820\app
```

双击 `SOP平台.exe` 会自动启动后端。启动器优先使用应用 `.venv` 或已配置的 Anaconda GPU
环境，没有时回退到交付包内的 Python 3.12 嵌入式运行时。随包运行时可保证网页、标注、SQLite
和报告功能；Windows 本机 GPU 推理需另外安装 CUDA、PyTorch、Ultralytics 和 OpenCV。
