$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
conda run -n design-rag python apps/rerank/scripts_download_bge_reranker.py
