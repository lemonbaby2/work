$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$InstallerRoot = $PSScriptRoot
$BuildRoot = Join-Path $env:TEMP "SOP分析平台_Windows_Build"
$DistRoot = Join-Path $InstallerRoot "dist"
$AiCandidates = @(
    "AdvancedInstaller.com",
    "${env:ProgramFiles}\Caphyon\Advanced Installer\AdvancedInstaller.com",
    "${env:ProgramFiles(x86)}\Caphyon\Advanced Installer\AdvancedInstaller.com"
)
$Ai = $AiCandidates | Where-Object { $_ -and ((Get-Command $_ -ErrorAction SilentlyContinue) -or (Test-Path $_)) } | Select-Object -First 1

if (-not $Ai) {
    throw "未找到 AdvancedInstaller.com。请先安装 Advanced Installer，并将其 CLI 加入 PATH。"
}

New-Item -ItemType Directory -Force -Path $BuildRoot, $DistRoot | Out-Null
& robocopy $ProjectRoot $BuildRoot /E /R:1 /W:1 /XD .venv .git __pycache__ build dist /XF *.pyc | Out-Null
if ($LASTEXITCODE -gt 7) { throw "复制部署文件失败，Robocopy 退出码 $LASTEXITCODE" }

Copy-Item -LiteralPath (Join-Path $InstallerRoot "启动SOP平台_Windows.bat") -Destination $BuildRoot -Force
Copy-Item -LiteralPath (Join-Path $InstallerRoot "requirements-windows.txt") -Destination $BuildRoot -Force

$Aip = Join-Path $InstallerRoot "SOP平台.aip"
if (-not (Test-Path $Aip)) {
    Write-Host "创建 Advanced Installer Professional 项目：$Aip"
    & $Ai /newproject $Aip -type professional
    if ($LASTEXITCODE -ne 0) { throw "Advanced Installer 无法创建项目，退出码 $LASTEXITCODE" }
}

& $Ai /edit $Aip /SetProperty 'ProductName=宁波SOP分析平台'
& $Ai /edit $Aip /SetProperty 'ProductVersion=1.0.20260819'
& $Ai /edit $Aip /SetPackageType x64
& $Ai /edit $Aip /SetOutputType ExeInside
& $Ai /edit $Aip /AddFolder APPDIR $BuildRoot
if ($LASTEXITCODE -ne 0) { throw "Advanced Installer 导入部署文件失败，退出码 $LASTEXITCODE" }
& $Ai /build $Aip
if ($LASTEXITCODE -ne 0) { throw "Advanced Installer 构建失败，退出码 $LASTEXITCODE" }
Write-Host "安装程序已生成到 Advanced Installer 项目配置的输出目录。"
