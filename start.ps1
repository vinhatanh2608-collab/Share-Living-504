param(
    [switch]$Desktop,
    [switch]$WebOnly,
    [switch]$BuildExe
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$BackendDir = "$Root\backend"
$FrontendDir = "$Root\frontend"


$env:Path += ";C:\Program Files\Docker\Docker\resources\bin;C:\ProgramData\DockerDesktop\version-bin;$env:USERPROFILE\.cargo\bin"

try {
if ($BuildExe) {
    Push-Location $FrontendDir
    try {
        if (-not (Test-Path "node_modules\.bin\vite.cmd")) {
            npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
        }
        npm.cmd run release:check
        if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
        if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { throw "Cargo is required to build the Windows installer." }
        npm.cmd run tauri build -- --bundles nsis
        if ($LASTEXITCODE) { throw "Installer build failed." }
        return
    } finally { Pop-Location }
}

function Test-Docker {
    cmd /c "docker info >nul 2>&1"
    return ($LASTEXITCODE -eq 0)
}

Write-Host "=== KHOI DONG SHARED LIVING (DATABASE + BACKEND + FRONTEND) ===" -ForegroundColor Cyan

Write-Host "[1/4] Checking Docker Desktop..."
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
    docker compose up -d --wait --remove-orphans --wait-timeout 15 localstack
    if ($LASTEXITCODE) { throw "LocalStack bucket readiness failed within the 15-second wait. Check docker compose logs localstack." }
    docker compose up -d --wait --remove-orphans --wait-timeout 120
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

Write-Host "Checking ports 8000 and 1420..."
# netstat avoids a slow Windows networking CIM provider on startup.
$listeners = netstat.exe -ano -p TCP
if ($LASTEXITCODE) { throw "Could not inspect TCP listeners." }
$owners = foreach ($line in $listeners) {
    if ($line -match '^\s*TCP\s+\S+:(8000|1420)\s+\S+\s+LISTENING\s+(\d+)') { [int]$Matches[2] }
}
foreach ($owner in ($owners | Sort-Object -Unique)) {
    $process = Get-Process -Id $owner -ErrorAction SilentlyContinue
    if (!$process) { continue }
    if ($owner -le 4 -or $process.ProcessName -match 'docker') { throw "Port reserved by $($process.ProcessName). Check Docker port mappings." }
    Stop-Process -InputObject $process -Force
    if (!$process.WaitForExit(3000)) { throw "Port owner $owner did not exit." }
}

function Wait-Http($url, $seconds, $service) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while (($remaining = $seconds - $timer.Elapsed.TotalSeconds) -ge 1) {
        try {
            $timeout = [int][Math]::Min(2, [Math]::Floor($remaining))
            if ((Invoke-WebRequest $url -UseBasicParsing -TimeoutSec $timeout).StatusCode -eq 200) { return }
        } catch {}
        Start-Sleep -Milliseconds 150
    }
    throw "$service did not respond at $url within $seconds seconds. Check the $service PowerShell window."
}

Push-Location $FrontendDir
try {
    if (!(Test-Path 'node_modules\.bin\vite.cmd')) {
        npm.cmd ci
        if ($LASTEXITCODE) { throw "Frontend dependency installation failed." }
    }
} finally { Pop-Location }
$env:VITE_API_URL = "http://127.0.0.1:8000/api/v1"
$env:VITE_WS_URL = "ws://127.0.0.1:8000/ws"
$env:VITE_STORAGE_URL = "http://127.0.0.1:9000"
if (!(Test-Path "$Root\.venv\Scripts\python.exe")) { throw "Missing backend virtual environment. Follow the README backend setup." }
Write-Host "[3/4] Starting Backend..."
# Use --workers 1 until rate limits, WebSocket state and circuit breakers use a shared store such as Redis.
Start-Process powershell.exe -WorkingDirectory $BackendDir -ArgumentList '-NoProfile -NoExit -Command & ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --ws-max-size 4096 --no-proxy-headers --timeout-keep-alive 5 --limit-concurrency 200 --limit-max-requests 5000 --backlog 512 --no-access-log'
$browserMode = $WebOnly -or !(Get-Command cargo -ErrorAction SilentlyContinue)
$command = if ($browserMode) { "npm.cmd run dev -- --strictPort" } else { "npm.cmd run tauri dev" }
Write-Host "[4/4] Starting Frontend ($command)..."
Start-Process powershell.exe -WorkingDirectory $FrontendDir -ArgumentList "-NoProfile -NoExit -Command $command"
Wait-Http "http://127.0.0.1:8000/docs" 15 Backend
if ($browserMode) {
    Wait-Http "http://127.0.0.1:1420" 20 Frontend
    Start-Process "http://127.0.0.1:1420"
}
} catch {
    Write-Host $_ -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
