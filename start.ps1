param(
    [switch]$Desktop,
    [switch]$WebOnly,
    [switch]$BuildExe
)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$BackendDir = "$Root\backend"
$FrontendDir = "$Root\frontend"
$VenvUvicorn = "$Root\.venv\Scripts\uvicorn.exe"

$env:Path += ";C:\Program Files\Docker\Docker\resources\bin;C:\ProgramData\DockerDesktop\version-bin;$env:USERPROFILE\.cargo\bin"

if ($BuildExe) {
    Push-Location $FrontendDir
    try {
        if (-not (Test-Path "node_modules\.bin\vite.cmd")) {
            npm.cmd ci
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
        npm.cmd run release:check
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { throw "Cargo is required to build the Windows installer." }
        npm.cmd run tauri build -- --bundles nsis
        exit $LASTEXITCODE
    } finally { Pop-Location }
}

function Test-Docker {
    cmd /c "docker info >nul 2>&1"
    return ($LASTEXITCODE -eq 0)
}

Write-Host "=== KHOI DONG SHARED LIVING (DATABASE + BACKEND + FRONTEND) ===" -ForegroundColor Cyan

# 1. Tu bat Docker Desktop & cho Engine san sang
if (-not (Test-Docker)) {
    $dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (-not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue) -and (Test-Path $dockerExe)) {
        Start-Process $dockerExe
    }
    Write-Host "[1/4] Dang bat Docker Desktop..." -ForegroundColor Yellow
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Docker) { break }
    }
}
if (-not (Test-Docker)) {
    Write-Host "LOI: Docker Engine chua chay. Hay mo Docker Desktop kiem tra." -ForegroundColor Red
    Read-Host "Nhan Enter de thoat"
    exit 1
}

# 2. Dong bo file .env
if ((Test-Path "$BackendDir\.env.example") -and -not (Test-Path "$BackendDir\.env")) {
    Copy-Item "$BackendDir\.env.example" "$BackendDir\.env"
}
# 3. Bat Database (MySQL, MongoDB, S3) & cho MySQL san sang
Push-Location $BackendDir
try {
    Write-Host "[2/4] Dang bat Database (MySQL, MongoDB, S3)..." -ForegroundColor Yellow
    docker compose up -d --wait --wait-timeout 120
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

# 4. Bat Backend FastAPI (cong 8000) o cua so thu nho
$port8000 = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $port8000) {
    Write-Host "[3/4] Dang bat Backend FastAPI (cong 8000)..." -ForegroundColor Green
    # Keep Uvicorn at one worker (--workers 1 without --reload). Rate-limit counters,
    # WebSocket mute and connection state, and circuit breakers require shared state
    # and WebSocket event delivery (for example, Redis) to support multiple workers.
    Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoExit", "-Command", "cd '$BackendDir'; & '$VenvUvicorn' app.main:app --reload --host 127.0.0.1 --port 8000 --ws-max-size 4096 --no-proxy-headers --timeout-keep-alive 5 --limit-concurrency 200 --limit-max-requests 5000 --backlog 512 --no-access-log"
    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Seconds 1
        try {
            $res = Invoke-WebRequest -Uri "http://127.0.0.1:8000/openapi.json" -UseBasicParsing -TimeoutSec 2
            if ($res.StatusCode -eq 200) { break }
        } catch {}
    }
} else {
    Write-Host "[3/4] Backend FastAPI da chay san o cong 8000." -ForegroundColor Green
}

# 5. Bat Frontend
if (Get-NetTCPConnection -LocalPort 1420 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port 1420 is already in use. Reuse the running frontend or stop it before restarting." -ForegroundColor Yellow
    exit 1
}

Push-Location $FrontendDir
$previousFrontend = @{
    VITE_API_URL = $env:VITE_API_URL
    VITE_WS_URL = $env:VITE_WS_URL
    VITE_STORAGE_URL = $env:VITE_STORAGE_URL
}
try {
    $env:VITE_API_URL = "http://127.0.0.1:8000/api/v1"
    $env:VITE_WS_URL = "ws://127.0.0.1:8000/ws"
    $env:VITE_STORAGE_URL = "http://127.0.0.1:9000"
    if (-not (Test-Path "node_modules\.bin\vite.cmd")) {
        npm.cmd ci
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    if (-not $WebOnly -and (Get-Command cargo -ErrorAction SilentlyContinue)) {
        Write-Host "[4/4] Dang mo cua so ung dung Desktop Tauri..." -ForegroundColor Cyan
        npm.cmd run tauri dev
    } else {
        Write-Host "[4/4] Dang mo giao dien ung dung tai http://127.0.0.1:1420 ..." -ForegroundColor Cyan
        Start-Job -ScriptBlock {
            for ($i = 0; $i -lt 20; $i++) {
                Start-Sleep -Seconds 1
                try {
                    $r = Invoke-WebRequest -Uri "http://127.0.0.1:1420" -UseBasicParsing -TimeoutSec 1
                    if ($r.StatusCode -eq 200) {
                        Start-Process "http://127.0.0.1:1420"
                        break
                    }
                } catch {}
            }
        } | Out-Null
        npm.cmd run dev
    }
} finally {
    foreach ($name in $previousFrontend.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousFrontend[$name], "Process")
    }
    Pop-Location
}
