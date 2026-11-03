[CmdletBinding()]
param(
    [string]$EnvFile,
    [Alias('Host')]
    [string]$ListenHost,
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $EnvFile) { $EnvFile = Join-Path $root '.env' }

if (Test-Path -LiteralPath $EnvFile) {
    foreach ($line in [System.IO.File]::ReadAllLines((Resolve-Path -LiteralPath $EnvFile))) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        if ($trimmed -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { continue }
        $name = $Matches[1]
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if (-not [System.Environment]::GetEnvironmentVariable($name, 'Process')) {
            [System.Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}

if ($ListenHost) { $env:DESIGN_RAG_MCP_HOST = $ListenHost }
if ($Port -gt 0) { $env:DESIGN_RAG_MCP_PORT = [string]$Port }
if (-not $env:DESIGN_RAG_MCP_HOST) { $env:DESIGN_RAG_MCP_HOST = '0.0.0.0' }
if (-not $env:DESIGN_RAG_MCP_PORT) { $env:DESIGN_RAG_MCP_PORT = '8040' }
if (-not $env:DESIGN_RAG_API_TOKEN) {
    throw 'DESIGN_RAG_API_TOKEN is missing. Put the read-only Agent Token in .env or the process environment.'
}

$bundledNode = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$node = if ($nodeCommand) { $nodeCommand.Source } elseif (Test-Path -LiteralPath $bundledNode) { $bundledNode } else { throw 'Node.js was not found.' }
$mcpRoot = Join-Path $root 'apps\mcp-server'
if (-not (Test-Path -LiteralPath (Join-Path $mcpRoot 'src\server.js'))) { throw 'MCP server entry point was not found.' }

Write-Host "Starting read-only Design_RAG MCP on $($env:DESIGN_RAG_MCP_HOST):$($env:DESIGN_RAG_MCP_PORT)"
Push-Location $mcpRoot
try {
    & $node 'src/server.js'
    if ($LASTEXITCODE -ne 0) { throw "MCP server exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}