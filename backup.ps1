$ErrorActionPreference = "Stop"
Push-Location (Join-Path $PSScriptRoot "backend")
try {
    & (Join-Path $PSScriptRoot ".venv\Scripts\python.exe") -m scripts.backup_db
    exit $LASTEXITCODE
} finally { Pop-Location }
