[CmdletBinding()]
param([string]$NodeImage = "node:22-bookworm")
$ErrorActionPreference = "Stop"
$adminWebPath = (Resolve-Path (Join-Path $PSScriptRoot "..\apps\admin-web")).Path
if (-not (Test-Path -LiteralPath (Join-Path $adminWebPath "package.json"))) { throw "Admin web package.json was not found." }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker Desktop is required. Start Docker Desktop and rerun this script." }
$mountArgument = "$adminWebPath`:/app"
& docker run --rm -v $mountArgument -w /app $NodeImage bash -lc "npm ci && npm run build"
if ($LASTEXITCODE -ne 0) { throw "Admin web build failed (Docker exit code: $LASTEXITCODE)." }
$indexPath = Join-Path $adminWebPath "dist\index.html"
if (-not (Test-Path -LiteralPath $indexPath)) { throw "Build finished without dist/index.html." }
$bytes = [System.IO.File]::ReadAllBytes($indexPath)
$prefix = [System.Text.Encoding]::UTF8.GetString($bytes, 0, [Math]::Min(512, $bytes.Length)).TrimStart().ToLowerInvariant()
if (-not ($prefix.StartsWith("<!doctype html") -or $prefix.StartsWith("<html"))) { throw "dist/index.html is invalid HTML." }
Write-Host "Build verified: $indexPath"
Write-Host "Open http://127.0.0.1:8030/admin/ after the full local backend is running."
