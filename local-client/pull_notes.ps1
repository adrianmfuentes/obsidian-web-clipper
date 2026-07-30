<#
.SYNOPSIS
    Obsidian Web Clipper - Local Pull Client (PowerShell)

.DESCRIPTION
    Connects to your Oracle server queue, writes received Markdown notes into
    the Inbox folder of your Obsidian vault, then acks them so the server can
    drop them from the queue. A note is only acked after it's been written to
    disk, so a crash mid-run just means it's fetched again next time -
    nothing is lost.

    Two modes:
      One-shot (default) : fetch whatever's pending once, then exit.
                            Good for Task Scheduler on a fixed interval.
      Watch (-Watch)      : long-poll the server so new notes land in your
                            vault within seconds of being clipped, instead of
                            waiting for the next scheduled run.

    Prefer the Python version (pull_notes.py) if you have Python installed
    - this script is the no-dependency alternative for Windows-only setups.

.PARAMETER Watch
    Long-poll the server continuously instead of doing a single pull.
    Run this instead of scheduling one-shot runs (Ctrl+C to stop).

.NOTES
    Schedule with Windows Task Scheduler (one-shot mode):
      Trigger 1: At log on
      Trigger 2: On a schedule - repeat every 30 minutes indefinitely
      Action   : Start a program
                 Program : powershell.exe
                 Arguments: -NonInteractive -ExecutionPolicy Bypass -File "C:\path\to\pull_notes.ps1"

    ...or run it once, long-lived, in watch mode instead:
      powershell -ExecutionPolicy Bypass -File pull_notes.ps1 -Watch
#>

param(
    [switch]$Watch
)

# ─── Configuration ─────────────────────────────────────────────────────────────
# Edit these three values. No other changes needed.

$SERVER_URL   = "https://YOUR_ORACLE_SERVER:8000"   # No trailing slash
$AUTH_TOKEN   = "CHANGE_ME_TO_A_LONG_RANDOM_STRING"
$INBOX_PATH   = "C:\Users\YOUR_USERNAME\Documents\ObsidianVault\Inbox"

# How long the server may hold a -Watch request open waiting for a new note
# before replying empty. Must stay comfortably under any reverse proxy's
# read-timeout (the README's Caddy example uses the default 60s).
$WAIT_TIMEOUT = 25

# ─── (Optional) override via environment variables ─────────────────────────────
if ($env:CLIPPER_SERVER_URL)  { $SERVER_URL = $env:CLIPPER_SERVER_URL }
if ($env:CLIPPER_AUTH_TOKEN)  { $AUTH_TOKEN = $env:CLIPPER_AUTH_TOKEN }
if ($env:CLIPPER_INBOX_PATH)  { $INBOX_PATH = $env:CLIPPER_INBOX_PATH }

# ─── Logging helper ────────────────────────────────────────────────────────────
function Write-Log {
    param([string]$Level, [string]$Message)
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")
    $color = switch ($Level) {
        "INFO"    { "Cyan"   }
        "SUCCESS" { "Green"  }
        "WARN"    { "Yellow" }
        "ERROR"   { "Red"    }
        default   { "White"  }
    }
    Write-Host "[$ts]  $($Level.PadRight(7)) $Message" -ForegroundColor $color
}

# ─── Config validation (also ensures the Inbox folder exists) ─────────────────
function Test-ClipperConfig {
    if ($SERVER_URL -like "*YOUR_ORACLE_SERVER*" -or $AUTH_TOKEN -like "*CHANGE_ME*") {
        Write-Log ERROR "Please edit the configuration at the top of pull_notes.ps1 before running."
        return $false
    }

    if (-not (Test-Path $INBOX_PATH)) {
        Write-Log WARN "Inbox not found. Creating: $INBOX_PATH"
        New-Item -ItemType Directory -Path $INBOX_PATH -Force | Out-Null
    }

    return $true
}

# ─── Ack previously-pulled notes so the server can drop them from the queue ───
function Confirm-NotesAcked {
    param([array]$Ids)

    if (-not $Ids -or $Ids.Count -eq 0) { return }

    try {
        $body = @{ ids = $Ids } | ConvertTo-Json
        Invoke-RestMethod `
            -Uri         "$($SERVER_URL.TrimEnd('/'))/ack-notes" `
            -Method      POST `
            -Headers     @{ "X-Auth-Token" = $AUTH_TOKEN } `
            -ContentType "application/json" `
            -Body        $body `
            -TimeoutSec  30 `
            -ErrorAction Stop | Out-Null
    }
    catch {
        # Not fatal: the notes are already written to disk. Worst case they
        # get re-delivered next run and the de-dupe suffix below kicks in.
        Write-Log WARN "Failed to ack $($Ids.Count) note(s) (will be re-delivered next run): $($_.Exception.Message)"
    }
}

# ─── Write every note in the response to the Inbox, then ack the saved ones ───
function Save-NotesAndAck {
    param($Response)

    $count = $Response.count
    if ($count -eq 0) { return 0 }

    Write-Log INFO "Received $count note(s). Writing to: $INBOX_PATH"

    $savedIds = @()
    foreach ($note in $Response.notes) {
        try {
            $filename = $note.filename
            if (-not $filename) { $filename = "note-$($note.id).md" }
            $filename = $filename -replace '[\\/*?:"<>|]', '-'
            if (-not $filename.EndsWith(".md")) { $filename += ".md" }

            $targetPath = Join-Path $INBOX_PATH $filename

            # Avoid overwriting existing files
            $counter = 1
            while (Test-Path $targetPath) {
                $stem       = [System.IO.Path]::GetFileNameWithoutExtension($filename)
                $targetPath = Join-Path $INBOX_PATH "$($stem)_$counter.md"
                $counter++
            }

            # Write UTF-8 without BOM (Obsidian prefers this)
            $utf8NoBom = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllText($targetPath, $note.markdown, $utf8NoBom)

            Write-Log SUCCESS "  [OK] Saved: $([System.IO.Path]::GetFileName($targetPath))"
            $savedIds += $note.id
        }
        catch {
            Write-Log ERROR "  [FAIL] Failed to write '$($note.filename)': $($_.Exception.Message)"
        }
    }

    Confirm-NotesAcked -Ids $savedIds
    return $savedIds.Count
}

# ─── One-shot: fetch whatever's pending right now, write it, ack it ──────────
function Invoke-SinglePull {
    $endpoint = "$($SERVER_URL.TrimEnd('/'))/pull-notes"
    Write-Log INFO "Connecting to $endpoint ..."

    try {
        $response = Invoke-RestMethod `
            -Uri        $endpoint `
            -Method     GET `
            -Headers    @{ "X-Auth-Token" = $AUTH_TOKEN } `
            -TimeoutSec 30 `
            -ErrorAction Stop
    }
    catch {
        $statusCode = $_.Exception.Response.StatusCode.value__
        if ($statusCode -eq 401) {
            Write-Log ERROR "Authentication failed - check AUTH_TOKEN."
        } elseif ($null -ne $statusCode) {
            Write-Log ERROR "Server returned HTTP $statusCode"
        } else {
            Write-Log ERROR "Connection failed: $($_.Exception.Message)"
        }
        return 0
    }

    if ($response.count -eq 0) {
        Write-Log INFO "No pending notes in queue."
        return 0
    }

    return Save-NotesAndAck -Response $response
}

# ─── Watch: long-poll so new notes land within seconds, not on a timer ───────
function Invoke-Watch {
    $endpoint = "$($SERVER_URL.TrimEnd('/'))/wait-notes"
    Write-Log INFO "Watching $endpoint (long-poll, Ctrl+C to stop) ..."

    while ($true) {
        try {
            $response = Invoke-RestMethod `
                -Uri        "$($endpoint)?timeout=$WAIT_TIMEOUT" `
                -Method     GET `
                -Headers    @{ "X-Auth-Token" = $AUTH_TOKEN } `
                -TimeoutSec ($WAIT_TIMEOUT + 10) `
                -ErrorAction Stop
        }
        catch {
            $statusCode = $_.Exception.Response.StatusCode.value__
            if ($statusCode -eq 401) {
                Write-Log ERROR "Authentication failed - check AUTH_TOKEN."
            } elseif ($null -ne $statusCode) {
                Write-Log ERROR "Server returned HTTP $statusCode"
            } else {
                Write-Log ERROR "Connection failed: $($_.Exception.Message)"
            }
            # Back off briefly so a down server doesn't spin-loop.
            Start-Sleep -Seconds 5
            continue
        }

        if ($response.count -gt 0) {
            Save-NotesAndAck -Response $response | Out-Null
        }
        # count == 0 just means the long-poll timed out with nothing new - loop again.
    }
}

# ─── Entry point ────────────────────────────────────────────────────────────────
$mode = if ($Watch) { "Watch mode" } else { "Pull Run" }
Write-Log INFO "--- Obsidian Web Clipper - $mode ($((Get-Date -Format 'yyyy-MM-dd HH:mm'))) ---"

if (-not (Test-ClipperConfig)) {
    exit 1
}

if ($Watch) {
    Invoke-Watch
}
else {
    $saved = Invoke-SinglePull
    Write-Log INFO "Done. $saved new note(s) added to your Obsidian Inbox."
}
