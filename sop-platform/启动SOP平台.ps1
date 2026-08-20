$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = "D:\Anaconda\envs\dl\python.exe"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "未找到 dl 环境：$pythonExe"
}

Set-Location -LiteralPath $projectRoot
Start-Process "http://127.0.0.1:8096"
& $pythonExe "$projectRoot\server.py"
