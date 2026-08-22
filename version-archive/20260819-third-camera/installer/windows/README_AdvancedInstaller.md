# SOP 平台 Windows 部署包

本目录用于在 Windows 工控机上使用 Advanced Installer 生成安装程序。

当前 Linux/DGX 主机不能直接运行 Advanced Installer，也不能可靠生成 Windows `.exe`。请在 Windows 10/11 或 Windows Server 上安装 Advanced Installer 20+，然后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_advanced_installer.ps1
```

脚本会：

1. 检查 `AdvancedInstaller.com` 是否在 PATH 或默认安装目录中。
2. 创建 `build\SOP分析平台_Windows` 部署目录。
3. 复制网页、服务代码、模型、配置、启动脚本和 Windows 依赖清单。
4. 调用 Advanced Installer CLI 打开/构建 `SOP分析平台.aip`（若项目由 Advanced Installer 首次导入，则按同目录清单添加文件）。
5. 输出 `dist\SOP分析平台安装程序.exe`。

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
