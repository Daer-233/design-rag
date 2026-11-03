<#
Build the React Chat UI through a Node/Vite in-memory bundle and persist the result with .NET.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$chatRoot = Join-Path $root 'apps\chat-web'
$bundledNode = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$node = if ($nodeCommand) { $nodeCommand.Source } elseif (Test-Path -LiteralPath $bundledNode) { $bundledNode } else { throw 'Node.js was not found. Install Node.js or use the Codex bundled runtime.' }

$viteMemoryBuild = @'
import { build } from 'vite'
const result = await build({ configFile: './vite.config.js', write: false, logLevel: 'silent' })
const outputs = Array.isArray(result) ? result.flatMap((item) => item.output || []) : (result.output || [])
for (const output of outputs) {
  const content = output.type === 'chunk' ? output.code : output.source
  const bytes = Buffer.isBuffer(content) ? content : Buffer.from(String(content), 'utf8')
  console.log('__DESIGN_RAG_FILE__\t' + output.fileName + '\t' + bytes.toString('base64'))
}
'@

Push-Location $chatRoot
try {
  $records = @($viteMemoryBuild | & $node --input-type=module -)
  if ($LASTEXITCODE -ne 0) { throw "Vite memory build failed with exit code $LASTEXITCODE" }
  $written = 0
  foreach ($record in $records) {
    if (-not $record.StartsWith('__DESIGN_RAG_FILE__')) { continue }
    $parts = $record.Split([char]9, 3)
    if ($parts.Count -ne 3) { throw 'Malformed Vite memory-build output.' }
    $relative = $parts[1].Replace('/', '\')
    if ([System.IO.Path]::IsPathRooted($relative) -or $relative.Contains("..\")) { throw "Unsafe Vite output path: $relative" }
    $target = Join-Path (Join-Path $chatRoot 'dist') $relative
    [System.IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
    [System.IO.File]::WriteAllBytes($target, [System.Convert]::FromBase64String($parts[2]))
    $written++
  }
  $entry = Join-Path $chatRoot 'dist\index.html'
  $entryText = [System.IO.File]::ReadAllText($entry, [System.Text.Encoding]::UTF8)
  if ($written -lt 3 -or -not $entryText.TrimStart().StartsWith('<!doctype html>', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Chat bundle validation failed: expected HTML, JavaScript, and CSS output.' }
  Write-Host "Chat bundle built successfully: $entry ($written files)"
} finally {
  Pop-Location
}
