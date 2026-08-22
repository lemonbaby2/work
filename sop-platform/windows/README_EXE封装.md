# Windows EXE 封装

`SOP平台.exe` 是原生 WinForms 启动器，与 `server.py`、`web`、`config`、`runtime`、`models`
保持在同一应用目录。它负责单实例启动、Python 环境发现、后端健康检查、浏览器打开、
日志落盘和服务停止；大模型、视频和数据库作为可更新的数据资产独立保存，不嵌进 EXE。
交付包内的 `python-runtime` 是 Windows 官方嵌入式运行时，用于保证网页、标注保存和 SQLite
功能可独立启动。

在 Windows PowerShell 中构建：

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\build_exe.ps1
```

2026-08-21 已在 DGX 上使用 Mono C# 编译器生成架构无关的 .NET Framework 4.5
WinForms PE 文件 `SOP平台.exe`。文件类型已核验为 Windows GUI PE，仍需在目标 Windows
主机完成界面、登录、三路视频和长时间重连验收。Windows 上如需从源码重建，继续使用上述
PowerShell 脚本和系统自带的 .NET Framework 编译器。

在包含 `server.py` 的完整本地应用目录中，双击 `SOP平台.exe` 后会自动启动后端，健康检查
通过后点击“打开网页”。“启动服务”和“停止服务”按钮用于手动恢复与维护。实时摄像头检测
需要 Python 环境安装 `requirements.txt` 中的 OpenCV、Ultralytics 和 PyTorch；标注、保存、
数据库、视频浏览和报告查看可直接使用交付包的嵌入式运行时。

只部署单个 `SOP平台.exe` 时，启动器会进入远程客户端模式，不要求 Windows 安装 Python；
点击“打开 DGX 三路原生监控”，填写 DGX 平台账号即可。只有需要在 Windows 本机运行完整
网页后端时，才应把 EXE 放回包含 `server.py` 和 Python 运行时的完整应用目录。
