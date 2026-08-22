# DGX 到 Windows 主机交付状态

## 已核实网络

```text
DGX:     192.168.1.129
Windows: 192.168.1.128
Windows MAC: 28:cd:c4:bb:ca:8f
```

两台主机位于同一 `192.168.1.0/24` 网段，Ping 正常，延迟约 8–15ms。

## 当前阻塞

Windows 主机 TCP 22 和 2222 未开放，所以 DGX 目前不能执行 SCP。TCP 445 开放，但没有 Windows 共享凭据，本次没有擅自改用 SMB 写入。

## 完成 SCP 的操作

1. 在 Windows 上解压前后端源码包或先取得 `installer/windows/Windows管理员_开启SCP接收.ps1`。
2. 以管理员 PowerShell 运行该脚本，安装并启动 OpenSSH Server。
3. 在 DGX 执行：

```bash
cd /home/xjai/sop_project/SOP分析平台_老板汇报版
./installer/linux/DGX发送到Windows主机.sh <Windows用户名> 192.168.1.128
```

4. 输入 Windows 登录密码后，文件将进入 Windows 桌面的 `SOP交付包` 目录。

DGX 本地备份位于 `/home/xjai/sop_backups/20260819_第三摄像头`。
