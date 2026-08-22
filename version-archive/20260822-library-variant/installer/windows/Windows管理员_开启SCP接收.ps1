$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "请右键 PowerShell，选择‘以管理员身份运行’，再执行本脚本。"
}

$capability = Get-WindowsCapability -Online | Where-Object Name -Like 'OpenSSH.Server*'
if ($capability.State -ne 'Installed') {
    Add-WindowsCapability -Online -Name $capability.Name | Out-Null
}

Set-Service -Name sshd -StartupType Automatic
Start-Service sshd

if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
}

$destination = Join-Path $env:USERPROFILE 'Desktop\SOP交付包'
New-Item -ItemType Directory -Force -Path $destination | Out-Null
Write-Host "OpenSSH/SCP 已开启。"
Write-Host "Windows 用户名：$env:USERNAME"
Write-Host "接收目录：$destination"
Write-Host "请将上述用户名提供给 DGX 端，并保持本机 IP 为 192.168.1.128。"
