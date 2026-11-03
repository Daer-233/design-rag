<#
Copies the verified dependency-free Chat UI fallback into the static directory.
Use this only while the local Node/Vite writer issue is unresolved. It does not
start the Backend and does not submit MinerU or GPT jobs.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "apps\chat-web\static-fallback.html"
$targetDir = Join-Path $root "apps\chat-web\dist"
$target = Join-Path $targetDir "index.html"

if (-not (Test-Path -LiteralPath $source)) {
    throw "Chat fallback source not found: $source"
}
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
$content = [System.IO.File]::ReadAllText($source)
if (-not $content.TrimStart().StartsWith("<!doctype html>", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Fallback source is not a valid HTML entry point: $source"
}
[System.IO.File]::WriteAllText($target, $content, [System.Text.UTF8Encoding]::new($false))
Write-Host "Chat fallback built: $target"