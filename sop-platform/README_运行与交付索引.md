# 宁波零部件 SOP 智能分析决策平台（五视频前沿算法版）

## 一、交付结果

这不是单独的“识别模型演示”，而是一套可继续扩展的装配 SOP 平台：固定相机录像、自动抽帧、网页标注、GPU训练、SOP顺序判断、现场告警、证据留存、灰度下发和 MES 对接均放在同一工程中。

本次在原三段视频基础上新增两段真实车间视频，已形成：

- 5段视频、15,163帧、累计505.43秒、30个SOP步骤；
- 新增两段无遮挡 H.264 演示成片，均为1620×720：左侧1280×720完整检测画面，右侧340×720独立SOP栏；
- YOLOE-26S开放词汇实例分割 + 四窗口重叠切片，用于大零件紧边框和螺钉/卡扣/孔位候选；
- 544张新增抽帧图、544份YOLO格式预标注、12个细粒度类别，全部标明“待人工复核”；
- YOLO26N学生模型已在RTX 4060上训练12轮，并导出PT和ONNX；
- 10张中文论文式可视化图，以及逐帧JSONL、候选框日志、六步快照和GPU训练记录。

## 二、直接运行

双击 `启动SOP平台.ps1`，或在 VSCode 按 `F5` 选择“启动：宁波SOP分析平台”，访问：

`http://127.0.0.1:8096`

固定环境：

```powershell
& 'D:\Anaconda\shell\condabin\conda-hook.ps1'
conda activate dl
python server.py
```

Python解释器：`D:\Anaconda\envs\dl\python.exe`。

## 三、新增两段视频

1. `web\media\仪表板SOP_视频四_40b5_YOLOE26_SAHI_无遮挡版.mp4`
2. `web\media\仪表板SOP_视频五_ecc57_YOLOE26_SAHI_无遮挡版.mp4`

顶部工作栏不再使用覆盖视频的定位方式；网页视频区域按9:4比例完整展示成片。大零件框采用实例分割掩膜紧边框、类别阈值、肤色排除、形状约束和跨帧平滑，降低把人员手臂误框成饰板的情况。小目标通过重叠切片放大后检测，并保留人工复核状态。

## 四、VSCode任务

- `启动：宁波SOP分析平台`：启动本地仪表盘和接口。
- `新增：两视频YOLOE26细粒度处理`：从原视频重新生成预标注、逐帧日志、快照和成片。
- `精修：两视频大零件框并重绘`：二次过滤大零件误检并重绘成片。
- `训练：YOLO26两视频蒸馏模型`：在RTX 4060上训练小目标学生模型。
- `输出：前沿算法中文可视化`：重新生成10张中文图表。
- 原三视频的YOLOv8和右侧SOP面板任务继续保留。

## 五、关键目录

- `web`：HTML仪表盘、五视频、快照和逐帧数据。
- `scripts\process_frontier_two_videos.py`：YOLOE-26S与重叠切片主处理脚本。
- `scripts\refine_frontier_boxes_and_rerender.py`：大零件框尺寸/位置精修与无遮挡重绘。
- `scripts\train_yolo26_bootstrap.py`：YOLO26N GPU蒸馏训练。
- `scripts\build_frontier_visual_report.py`：10张中文图表生成脚本。
- `datasets\新增两视频_YOLOE26_SAHI细粒度预标注_待人工复核`：544张图及12类候选标签。
- `models\yoloe-26s-seg.pt`：离线开放词汇教师模型。
- `models\yolo26n_两视频小目标蒸馏_待人工验收.pt`：在线学生模型候选。
- `models\yolo26n_两视频小目标蒸馏_待人工验收.onnx`：跨平台部署模型候选。
- `analysis\新增两视频_前沿算法可视化`：10张中文结果图。
- `docs\08_新增两视频与前沿SOP架构说明.md`：算法选择、行业参考和落地路线。

## 六、必须讲清楚的真实性边界

- 自动预标注不是人工真值。所有新增框必须由标注员逐框确认或删除，质量人员抽检后才能进入正式训练集。
- YOLO26N当前指标衡量“学生模型与教师自动标签的一致性”，不是量产精度。最终报告为精确率74.51%、召回率16.77%、mAP50 17.13%、mAP50-95 11.33%；召回率偏低，当前模型不得直接下发量产。
- 测试视频没有真实PSet、扭矩、角度和MES回执，因此平台始终保持 `HOLD`，不会伪造合格放行。
- 量产模型必须在固定相机、固定治具、稳定补光的宁波现场数据上训练，并使用按工单/产品SN隔离的人工冻结测试集验收。

## 七、正式上线前还要做什么

1. 工艺确认每个步骤、关键零件、孔位和允许等待时间。
2. 固定相机与补光，采集正常、漏装、错序、遮挡和返工样本。
3. 完成新增544张预标注的人工复核，并继续补充难例。
4. 重新训练闭集YOLO26/YOLOv8模型，按关键漏装拦截率、错序拦截率和每百件误报率验收。
5. 接入电动工具控制器，读取PSet、扭矩、角度和拧紧结果。
6. 与MES测试环境完成工单下发、结果上传、离线补传、幂等和审计联调。
7. 先灰度到一个工位，验证节拍、延迟、误报、网络中断和模型回滚，再扩展到更多相机。

## 八、现场摄像头检测与 DGX 五分钟证据采集

新增脚本 `scripts/camera_capture.py`，用于现场验证。它读取 USB/UVC、GigE/RTSP 或本地视频，使用 Ultralytics 模型画检测框，并且每 300 秒封存一段 MP4，同时写入逐帧 JSONL 和元数据。脚本不会把视觉结果直接当成 MES 放行结果。

当前 DGX Spark 新增 Insta360 Link 2C，稳定路径为 `/dev/v4l/by-id/usb-Insta360_Insta360_Link_2C-video-index0`。推荐实时模式为 MJPEG 1920x1080@30；运行 `python3 scripts/probe_uvc_camera.py --test` 可重新输出能力和采集测试报告。运行 `python3 scripts/sync_models_to_spark.py --apply` 可校验并同步视觉模型到 `/home/xjai/sop-model-store`。

### 1. 安装环境

```bash
cd /path/to/SOP分析平台_老板汇报版
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. 先用本地视频做 20 秒演练

```bash
python scripts/camera_capture.py \
  --source web/media/仪表板SOP_视频四_40b5_YOLOE26_SAHI_无遮挡版.mp4 \
  --model models/yolo26n.pt \
  --segment-seconds 10 \
  --test-seconds 20 \
  --no-preview \
  --dgx-dir /mnt/dgx/sop/camera_segments
```

如果当前模型不能加载，可先验证采集和编码链路：

```bash
python scripts/camera_capture.py --source 0 --no-detect --segment-seconds 10 --test-seconds 20
```

### 3. 接入现场摄像头

USB 摄像头通常使用 `--source 0`；RTSP/GigE 使用完整 URL。把 DGX 的 NFS/NAS 目录挂载到 IPC 后，用 `--dgx-dir` 指向挂载点。若现场不能挂载共享目录，改用 SFTP：

```bash
python scripts/camera_capture.py \
  --source 0 --model models/yolo26n.pt --preview \
  --dgx-dir /mnt/dgx/sop/camera_segments \
  --segment-seconds 300
```

或：

```bash
python scripts/camera_capture.py --source rtsp://user:password@camera/stream \
  --sftp-host 192.168.10.20 --sftp-user sop --sftp-password 'CHANGE_ME' \
  --sftp-remote-dir /data/sop/camera_segments
```

输出目录结构：

```text
runtime/camera_capture/
├── staging/                 # 正在写入的分段，异常退出时可人工处理
└── segments/
    ├── CAM_TOP_*.mp4
    ├── CAM_TOP_*.jsonl
    └── CAM_TOP_*.metadata.json
```

### 4. DGX 端检查

```bash
find /mnt/dgx/sop/camera_segments -type f -mmin -15 -ls
ffprobe -v error -show_entries format=duration:stream=width,height,codec_name \
  /mnt/dgx/sop/camera_segments/CAM_TOP_*.mp4
```

当前工作区未检测到 `/dev/video*`，因此不能在开发主机上替你声称已完成真实相机验证；接入相机后先用 `--no-detect` 验证采集，再去掉该参数验证模型。

本项目已在 DGX Spark（NVIDIA GB10、aarch64）建立 `.venv` 并验证：`opencv-python 5.0.0`、`ultralytics 8.4.121`、`torch 2.13.0+cu130`、`CUDA available=True`、`paramiko 5.0.0` 和 GitHub CLIP 均可导入。`/dev/video0` 已实测 640x480/30 FPS，模型推理和 DGX 本地归档演练通过。若需要使用命令行 `ffmpeg/ffprobe`，还需由有 sudo 权限的管理员执行 `sudo apt-get install ffmpeg`；OpenCV 采集脚本本身不依赖该系统包。

## 九、标注数据与算法选型 API

平台标注页直接读取现有逐帧预标注、小目标候选和人工标注。人工新框默认进入 `pending`，质量人员确认后才可由导出接口进入训练数据管道；原始预标注文件保持只读，审核结果单独写入 `runtime/annotation_reviews.jsonl`。

- `GET /api/annotations?video=video_de02&time=0&source=all&status=all`：按视频和时间读取当前帧候选及人工标注。
- `POST /api/annotations`：写入归一化 `xyxy` 人工标注，后端兼容并转换历史像素坐标。
- `POST /api/annotations/review`：记录确认、驳回、待修正等审核结果。
- `GET /api/annotations/stats`：返回预标注、候选、人工标注和审核统计。
- `GET /api/annotations/export?status=human_confirmed`：只导出当前已确认标注快照。
- `GET /api/algorithm-comparison`：返回 YOLO26、RT-DETRv2、D-FINE 的本地证据、风险和验收建议。

算法选型依据见 `docs/09_工业检测算法三模型选型与验收.md`。RT-DETRv2和D-FINE未在本项目冻结测试集实测前，页面不会显示虚构的精度或速度。

## 十、网页实时 YOLOv11 检测

“现场监控”页面已加入实时相机检测区域。点击“启动实时检测”后，后端按当前下拉选择打开对应摄像头槽位，使用 `models/yolo11n.pt` 在 DGX Spark `cuda:0` 推理，并通过 `/api/camera/mjpeg` 向浏览器发送带框画面；`/api/camera/status` 返回 FPS、分辨率、推理耗时和当前目标数，同时携带三路槽位状态。

DGX Linux 一键启动：

```bash
chmod +x 启动SOP平台_DGX.sh
./启动SOP平台_DGX.sh
```

需要改成 RTSP 或历史视频输入时：

```bash
SOP_CAMERA_SOURCE='rtsp://user:password@camera/stream' ./启动SOP平台_DGX.sh
```

页面地址：`http://127.0.0.1:8096`。同一时刻只能有一个进程独占某一路 USB 摄像头；若页面提示无法打开相机，先用 `fuser -v /dev/video0 /dev/video1 /dev/video2` 查找正在占用的采集进程。

三相机配置使用 `SOP_CAMERA_SOURCE_0`、`SOP_CAMERA_SOURCE_1` 和 `SOP_CAMERA_SOURCE_2`，网页点击“启动实时检测”后按当前下拉选择启动对应一路，切换时会先停止旧流再打开新流：

```bash
SOP_CAMERA_SOURCE_0=0 SOP_CAMERA_SOURCE_1=1 SOP_CAMERA_SOURCE_2=2 ./启动SOP平台_DGX.sh
```

### 当前 USB 摄像头接入说明（2026-08-19）

当前主机实际发现两组 USB 视频设备：

- `Jieli Technology USB Composite Device`：稳定采集路径 `/dev/v4l/by-id/usb-Jieli_Technology_USB_Composite_Device-video-index0`，对应 `/dev/video0`，已验证 1280×720、30 FPS；启动脚本默认使用这一组。
- `webcamproduct`：稳定采集路径 `/dev/v4l/by-id/usb-webcamvendor_webcamproduct_00000000-video-index0`，对应 `/dev/video2`；`/dev/ttyACM0` 是它的 USB 串口/控制通道。

UVC/USB 摄像头不是网口设备，因此没有可修改或固定的摄像头 MAC/IP。网页“现场监控”页的“摄像头身份、串口和主机网络”区域会显示 USB VID/PID、稳定设备路径、串口和采集主机的 MAC/IP。当前主机的 Wi-Fi 地址由系统动态读取，不能误当成摄像头地址。若要给摄像头固定 IP，必须改用 GigE/RTSP 摄像头，再在交换机或 DHCP 服务器做地址保留。

网页可直接完成以下操作：

- `启动实时检测`：按当前选择打开摄像头，使用低缓存采集，页面通过 MJPEG 只保留最新帧以降低延迟。
- `保存截图`：保存带 YOLO 框的 JPG。
- `开始录制` / `停止录制`：保存带框 MP4、逐帧 JSONL 和 metadata 文件。
- 标注页人工框选后点击 `保存当前标注`：同时保存 JSON 标注和当前帧带框 JPG。

以上证据默认保存在 `/home/xjai/Desktop/sop xjai/摄像头证据` 和 `/home/xjai/Desktop/sop xjai/标注图片`。目录由 `SOP_DESKTOP_DIR` 控制，可在启动脚本中改成现场共享盘路径。对应接口为：

- `GET /api/device/inventory`
- `POST /api/camera/snapshot?camera=0`
- `POST /api/camera/record/start?camera=0`
- `POST /api/camera/record/stop?camera=0`

实时服务默认 `12 FPS + imgsz=640 + CUDA half + camera buffer=1`，优先降低端到端排队延迟；如果要更高帧率，可设置 `SOP_CAMERA_MAX_FPS=20`，但需现场重新测 GPU、编码和浏览器带宽。

### Label Studio 视频标注

有 Docker 权限时：

```bash
./scripts/start_label_studio.sh
```

Label Studio 默认地址 `http://127.0.0.1:8080`，网页“标注与模型对比”页会自动检查并提供入口。`deploy/label-studio/docker-compose.yml` 将 `web/media` 以只读方式挂入标注容器；正式环境应改为 DGX/NAS 对象存储和内网 HTTPS。

### 模型性能对比与中文图表

```bash
.venv/bin/python scripts/benchmark_detection_models.py --device 0 --limit 24
```

报告位于 `web/analysis/model_benchmark/`，包含 YOLOv11n、YOLO26n、YOLO26n 工业学生模型和 YOLOv8-World 的延迟、FPS、稳定性、逐图热力图和工业部署散点图。当前 12 类标签仍是待人工复核预标注，报告中的自定义模型指标是“一致性代理指标”，不能替代人工冻结测试集精度。
