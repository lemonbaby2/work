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

双击 `SOP平台.exe` 后会自动启动后端，健康检查通过后点击“打开网页”。“启动服务”和
“停止服务”按钮用于手动恢复与维护。实时摄像头检测
需要 Python 环境安装 `requirements.txt` 中的 OpenCV、Ultralytics 和 PyTorch；标注、保存、
数据库、视频浏览和报告查看可直接使用交付包的嵌入式运行时。
