param(
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython) {
        $result = & $venvPython -c "import sys; sys.path.insert(0, 'apps/backend'); from app.db.migrations import apply_migrations; print('\n'.join(apply_migrations()) or 'No pending migrations.')"
    } elseif (Get-Command conda -ErrorAction SilentlyContinue) {
        $result = conda run -n design-rag python -c "import sys; sys.path.insert(0, 'apps/backend'); from app.db.migrations import apply_migrations; print('\n'.join(apply_migrations()) or 'No pending migrations.')" --no-capture
    } else { throw 'Neither project .venv nor conda is available.' }
    if (-not $Quiet) { Write-Host $result }
}
finally {
    Pop-Location
}
