$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

conda run -n design-rag python apps/embedding/scripts_download_bge_m3.py
