# SOP 平台 Windows 部署包

本目录用于在 Windows 工控机上使用 Advanced Installer 生成安装程序。

当前 Linux/DGX 主机不能直接运行 Advanced Installer，也不能可靠生成 Windows `.exe`。请在 Windows 10/11 或 Windows Server 上安装 Advanced Installer 20+，然后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_advanced_installer.ps1
```

脚本会：

1. 检查 `AdvancedInstaller.com` 是否在 PATH 或默认安装目录中。
2. 在 Windows 临时目录创建 `SOP分析平台_Windows_Build` 部署目录。
3. 复制网页、服务代码、模型、配置、启动脚本和 Windows 依赖清单。
4. 若项目不存在，自动创建 Professional 项目并导入部署目录。
5. 设置 x64 和 `ExeInside` 后调用 Advanced Installer CLI 构建。

输出文件位置由 Advanced Installer 的 Build 配置决定，建议设置为 `dist\SOP分析平台安装程序.exe`。

## 从 DGX 接收交付包

Windows 当前需要先开放 OpenSSH/SCP。以管理员身份运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Windows管理员_开启SCP接收.ps1
```

脚本会安装并启动 OpenSSH Server、开放 TCP 22，并创建桌面 `SOP交付包` 目录。之后在 DGX 执行：

```bash
./installer/linux/DGX发送到Windows主机.sh <Windows用户名> 192.168.1.128
```

推荐安装方式：安装包只负责安装应用目录和快捷方式，Python 运行时由工控机管理员预装 Python 3.12，或在 Advanced Installer 中添加 Python Embedded Runtime。安装后默认浏览器地址为 `http://127.0.0.1:8096`。

## Windows 网络摄像头 3

安装后进入现场监控页，将第三摄像头 RTSP 地址填写为厂家 App 提供的完整地址，例如：

```text
rtsp://用户名:密码@192.168.1.135:554/厂家实际路径
```

摄像头身份已经登记为：

```text
IP:  192.168.1.135
MAC: c4:3c:b0:be:40:e8
```

必须点击“测试 RTSP”通过后再点击“保存绑定”。不要仅凭 IP 或 MAC 推测 RTSP 路径。

服务端会先检查摄像头的 TCP 可达性，再调用 FFmpeg 检查视频流；如果提示 `No route to host` 或“网络不可达”，请先在摄像头 App、路由器 DHCP 静态租约和工控机网卡上恢复同一局域网连接。根地址 `/` 不能作为实际视频流地址。
