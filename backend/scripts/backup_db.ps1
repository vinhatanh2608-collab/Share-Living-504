param(
    [switch]$Daily,
    [string]$Restore,
    [switch]$ConfirmReplace,
    [switch]$RegisterTask
)
$ErrorActionPreference = "Stop"
$backend = Split-Path $PSScriptRoot -Parent
$root = Split-Path $backend -Parent
$python = Join-Path $root ".venv\Scripts\python.exe"
if (!(Test-Path -LiteralPath $python)) { throw "Project Python environment is missing: $python" }
if ($RegisterTask) {
    $action = New-ScheduledTaskAction -Execute $python -Argument "-m scripts.backup_db --daily" -WorkingDirectory $backend
    $trigger = New-ScheduledTaskTrigger -Daily -At "02:00"
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "Shared Living DB Backup" -Action $action -Trigger $trigger -Settings $settings
    return
}
$arguments = @("-m", "scripts.backup_db")
if ($Daily) { $arguments += "--daily" }
if ($Restore) { $arguments += @("--restore", (Resolve-Path -LiteralPath $Restore).Path) }
if ($ConfirmReplace) { $arguments += "--confirm-replace" }
Push-Location $backend
try {
    & $python @arguments
    exit $LASTEXITCODE
} finally { Pop-Location }
