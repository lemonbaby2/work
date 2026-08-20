$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$source = Join-Path $root "windows\SopPlatformLauncher.cs"
$output = Join-Path $root "SOP平台.exe"
$framework = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $framework)) {
    $framework = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path -LiteralPath $framework)) { throw "Windows .NET Framework C# compiler was not found." }
& $framework /nologo /utf8output /target:winexe /optimize+ `
    /reference:System.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll `
    "/out:$output" $source
if ($LASTEXITCODE -ne 0) { throw "SOP平台.exe build failed with exit code $LASTEXITCODE" }
Get-Item -LiteralPath $output | Select-Object FullName,Length,LastWriteTime
