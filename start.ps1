param(
    [switch]$Desktop
)

$ErrorActionPreference = "Continue"
$Root = "D:\qlct"
$BackendDir = "$Root\backend"
$FrontendDir = "$Root\frontend"
$VenvUvicorn = "$Root\.venv\Scripts\uvicorn.exe"

$env:Path += ";C:\Program Files\Docker\Docker\resources\bin;C:\ProgramData\DockerDesktop\version-bin;$env:USERPROFILE\.cargo\bin"

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
@"
VITE_API_URL=http://127.0.0.1:8000/api/v1
VITE_WS_URL=ws://127.0.0.1:8000/ws
"@ | Set-Content -Encoding utf8 "$FrontendDir\.env"

# 3. Bat Database (MySQL, MongoDB, S3) & cho MySQL san sang
Push-Location $BackendDir
try {
    Write-Host "[2/4] Dang bat Database (MySQL, MongoDB, S3)..." -ForegroundColor Yellow
    docker compose up -d | Out-Null
    for ($i = 0; $i -lt 30; $i++) {
        cmd /c "docker compose exec -T mysql mysqladmin ping -h 127.0.0.1 --silent >nul 2>&1"
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 2
    }
} finally {
    Pop-Location
}

# 4. Bat Backend FastAPI (cong 8000) o cua so thu nho
$port8000 = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $port8000) {
    Write-Host "[3/4] Dang bat Backend FastAPI (cong 8000)..." -ForegroundColor Green
    Start-Process powershell -WindowStyle Minimized -ArgumentList "-NoExit", "-Command", "cd '$BackendDir'; & '$VenvUvicorn' app.main:app --reload --host 127.0.0.1 --port 8000"
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

# 5. Giai phong cong 1420 cu (neu ket) & bat Frontend
Get-NetTCPConnection -LocalPort 1420 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

Push-Location $FrontendDir
try {
    if (-not (Test-Path "node_modules")) { npm install }
    if ($Desktop -and (Get-Command cargo -ErrorAction SilentlyContinue)) {
        Write-Host "[4/4] Dang mo cua so ung dung Desktop Tauri..." -ForegroundColor Cyan
        npm run tauri dev
    } else {
        Write-Host "[4/4] Dang mo giao dien ung dung tai http://localhost:1420 ..." -ForegroundColor Cyan
        Start-Job -ScriptBlock {
            for ($i = 0; $i -lt 20; $i++) {
                Start-Sleep -Seconds 1
                try {
                    $r = Invoke-WebRequest -Uri "http://localhost:1420" -UseBasicParsing -TimeoutSec 1
                    if ($r.StatusCode -eq 200) {
                        Start-Process "http://localhost:1420"
                        break
                    }
                } catch {}
            }
        } | Out-Null
        npm run dev
    }
} finally {
    Pop-Location
}
