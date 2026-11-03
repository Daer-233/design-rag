param(
    [switch]$Reload,
    [Alias('Host')]
    [string]$ListenHost = "0.0.0.0",
    [int]$Port = 8030
)

$reloadArgs = @()
if ($Reload) { $reloadArgs = @("--reload") }

$venvPython = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    & $venvPython -m uvicorn app.main:app --app-dir apps/backend --host $ListenHost --port $Port @reloadArgs
} elseif (Get-Command conda -ErrorAction SilentlyContinue) {
    conda run -n design-rag python -m uvicorn app.main:app --app-dir apps/backend --host $ListenHost --port $Port @reloadArgs
} else { throw 'Neither project .venv nor conda is available.' }
