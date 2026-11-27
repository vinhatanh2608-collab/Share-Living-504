$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$env:Path += ";C:\Program Files\Docker\Docker\resources\bin"
$tunnel = $null
$preview = $null
$backend = $null

function Wait-Web([string]$url, [int]$seconds) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt $seconds) {
        try {
            if ((Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { return }
        } catch {}
        Start-Sleep -Milliseconds 300
    }
    throw "Khong ket noi duoc: $url"
}

try {
    $cloudflared = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
    if (!$cloudflared) { throw "Chua cai cloudflared. Chay: winget install Cloudflare.cloudflared" }
    if (!(Test-Path "$root\frontend\node_modules\vite\bin\vite.js")) { throw "Chay npm ci trong frontend truoc." }
    Push-Location "$root\backend"
    try {
        docker compose unpause
        docker compose up -d --wait --wait-timeout 120
        if ($LASTEXITCODE) { throw "Docker Compose khong khoi dong duoc." }
    } finally { Pop-Location }

    $python = "$root\.venv\Scripts\python.exe"
    if (!(Test-Path $python)) { throw "Chua cai moi truong Python .venv. Xem README." }
    try { Wait-Web "http://127.0.0.1:8000/docs" 2 } catch {
        $backend = Start-Process $python -WorkingDirectory "$root\backend" -WindowStyle Hidden -PassThru -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --ws-max-size 4096"
        Wait-Web "http://127.0.0.1:8000/docs" 20
    }

    # The configured owner account must exist before public registration is reachable.
    Push-Location "$root\backend"
    try {
        $ownerCheck = @"
import asyncio
from sqlalchemy import select
from app.core.config import settings
from app.core.database import Resources
from app.models.mysql_models import User
from app.services.storage_service import receipt_proxy_key

async def check():
    resources = Resources()
    try:
        async with resources.sql() as sql:
            return await sql.scalar(select(User.id).where(User.username == settings().owner_username, User.active.is_(True)))
    finally:
        await resources.close()

if not asyncio.run(check()):
    raise SystemExit(1)
print(receipt_proxy_key())
"@
        $receiptKey = & $python -c $ownerCheck
        if ($LASTEXITCODE) { throw "Tao tai khoan OWNER_USERNAME cuc bo truoc khi mo lien ket cong khai." }
        if ($receiptKey -notmatch '^[a-f0-9]{64}$') { throw "Khong the tao khoa proxy bien lai." }
        $env:SHARED_LIVING_RECEIPT_PROXY_KEY = $receiptKey
    } finally { Pop-Location }

    $listeners = netstat.exe -ano -p TCP
    if ($listeners -match '^\s*TCP\s+\S+:1420\s+\S+\s+LISTENING\s+') {
        throw "Cong 1420 dang duoc su dung. Dong cua so frontend cu de khoi dong ban HTTPS."
    }
    $log = Join-Path $root "frontend\node_modules\.cloudflared-public.log"
    $tunnel = Start-Process $cloudflared.Source -ArgumentList 'tunnel --url http://127.0.0.1:1420 --no-autoupdate' -RedirectStandardError $log -WindowStyle Hidden -PassThru
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $publicUrl = $null
    while ($timer.Elapsed.TotalSeconds -lt 35 -and !$tunnel.HasExited) {
        $match = [regex]::Match([string](Get-Content -LiteralPath $log -Raw), 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($match.Success) { $publicUrl = $match.Value; break }
        Start-Sleep -Milliseconds 300
    }
    if (!$publicUrl) { throw "Cloudflare khong cap URL. Xem $log" }

    $env:SHARED_LIVING_PUBLIC_HOST = ([uri]$publicUrl).Host
    $env:VITE_PUBLIC_PROXY = "1"
    npm.cmd --prefix "$root\frontend" run build
    if ($LASTEXITCODE) { throw "Khong the build frontend." }
    $preview = Start-Process (Get-Command node.exe).Source -WorkingDirectory "$root\frontend" -ArgumentList 'node_modules/vite/bin/vite.js preview' -WindowStyle Hidden -PassThru
    Wait-Web "http://127.0.0.1:1420" 20
    Write-Host "Mo tren dien thoai bang 4G hoac Wi-Fi: $publicUrl" -ForegroundColor Green
    Write-Host "HTTPS cho phep su dung camera neu ban cap quyen trong trinh duyet."
    Write-Host "Giu cua so nay de duy tri ket noi. Nhan Ctrl+C de dong lien ket."
    while (!$tunnel.HasExited -and !$preview.HasExited) { Start-Sleep -Seconds 1 }
    throw "Cloudflare hoac frontend da dung. Xem $log"
} catch {
    Write-Host $_ -ForegroundColor Red
    exit 1
} finally {
    foreach ($process in @($tunnel, $preview, $backend)) {
        if ($process -and !$process.HasExited) { Stop-Process -InputObject $process }
    }
}
