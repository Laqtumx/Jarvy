$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Write-Host "`nJarvy installed. Activate with: .\.venv\Scripts\Activate.ps1"
Write-Host "Run: jarvy chat"
Write-Host "Edit .env to add GEMINI_API_KEY and configure allowed directories."
