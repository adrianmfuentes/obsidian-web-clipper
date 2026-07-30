<#
.SYNOPSIS
    Registers the Obsidian Clipper pull task in Windows Task Scheduler.

.DESCRIPTION
    Creates two triggers:
      - At every user log on
      - Every 30 minutes while the PC is running

    Runs via pythonw.exe (the windowless twin of python.exe), so no console
    window flashes into the foreground every 30 minutes — pull_notes.py logs
    to pull_notes.log next to itself instead. Run once as Administrator.
    To remove the task, run unschedule_task.ps1.

.NOTES
    Run with: powershell -ExecutionPolicy Bypass -File schedule_task.ps1
#>

# ─── Config ────────────────────────────────────────────────────────────────────
$TaskName   = "Obsidian Clipper Pull"
$ScriptPath = "$PSScriptRoot\pull_notes.py"

# Auto-detect the python executable (prefers the one in PATH)
$PythonExe  = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    Write-Host "ERROR: python not found in PATH. Install Python and try again." -ForegroundColor Red
    exit 1
}

# Prefer pythonw.exe (windowless) over python.exe — python.exe is a console
# app, so Task Scheduler would flash a terminal window into the foreground
# on every trigger. pythonw.exe ships alongside python.exe in every standard
# CPython install (same folder), so this should always resolve.
$PythonwExe = Join-Path (Split-Path $PythonExe -Parent) "pythonw.exe"
if (-not (Test-Path $PythonwExe)) {
    Write-Host "WARNING: pythonw.exe not found next to python.exe - falling back to python.exe." -ForegroundColor Yellow
    Write-Host "         Scheduled runs will briefly show a console window." -ForegroundColor Yellow
    $PythonwExe = $PythonExe
}

Write-Host "Using Python: $PythonwExe" -ForegroundColor Cyan
Write-Host "Script path : $ScriptPath" -ForegroundColor Cyan

# ─── Build task components ─────────────────────────────────────────────────────
$action = New-ScheduledTaskAction `
    -Execute  $PythonwExe `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $PSScriptRoot

# Trigger 1: at every log on (fires as soon as you turn on the PC and log in)
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn

# Trigger 2: repeat every 30 minutes while the session is active
# Uses a one-shot trigger in the past + RepetitionInterval to make it recurring
$triggerRepeat = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).Date `
    -RepetitionInterval  (New-TimeSpan -Minutes 30) `
    -RepetitionDuration  ([TimeSpan]::MaxValue)

# -StartWhenAvailable  : run if PC was off during a scheduled time
# -RunOnlyIfNetworkAvailable : skip if no internet
# -MultipleInstances IgnoreNew : don't stack runs
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew

# ─── Register ──────────────────────────────────────────────────────────────────
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task already exists – updating it." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName   $TaskName `
    -Action     $action `
    -Trigger    @($triggerLogon, $triggerRepeat) `
    -Settings   $settings `
    -RunLevel   Highest `
    -Description "Pulls clipped web articles from Oracle server into Obsidian Inbox." `
    -Force | Out-Null

Write-Host ""
Write-Host "Task registered successfully!" -ForegroundColor Green
Write-Host "  Name     : $TaskName" -ForegroundColor White
Write-Host "  Triggers : At log on  +  every 30 minutes" -ForegroundColor White
Write-Host "  Script   : $ScriptPath" -ForegroundColor White
Write-Host "  Runs silently (no console window) - check pull_notes.log next to the script for output." -ForegroundColor White
Write-Host ""
Write-Host "To remove it, run: .\unschedule_task.ps1" -ForegroundColor DarkGray
