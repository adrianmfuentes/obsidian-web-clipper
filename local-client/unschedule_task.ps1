<#
.SYNOPSIS
    Removes the Obsidian Clipper pull task from Windows Task Scheduler.

.NOTES
    Run with: powershell -ExecutionPolicy Bypass -File unschedule_task.ps1
#>

$TaskName = "Obsidian Clipper Pull"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if (-not $task) {
    Write-Host "Task '$TaskName' not found – nothing to remove." -ForegroundColor Yellow
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

Write-Host "Task '$TaskName' removed successfully." -ForegroundColor Green
