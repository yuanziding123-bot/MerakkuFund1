$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$python = if (Test-Path $bundledPython) { $bundledPython } else { "python" }
$logDir = Join-Path $repoRoot ".local"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

& $python -m uvicorn polyagents.web.server:app `
    --host 127.0.0.1 `
    --port 8000 `
    --log-level critical `
    *> (Join-Path $logDir "polyagents-web.log")
