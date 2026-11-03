param([switch]$Reload)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$args = @('-n','design-rag','python','-m','uvicorn','rerank_app.main:app','--app-dir','apps/rerank','--host','127.0.0.1','--port','8032')
if ($Reload) { $args += '--reload' }
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    & $venvPython -m uvicorn rerank_app.main:app --app-dir apps/rerank --host 127.0.0.1 --port 8032 $(if ($Reload) { '--reload' })
} elseif (Get-Command conda -ErrorAction SilentlyContinue) { conda run @args }
else { throw 'Neither project .venv nor conda is available.' }
