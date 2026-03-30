<#
.SYNOPSIS
    Obsidian Web Clipper – Local Pull Client (PowerShell)

.DESCRIPTION
    Polls your Oracle server's /pull-notes endpoint and writes received
    Markdown notes into the Inbox folder of your Obsidian vault.

    Prefer the Python version (pull_notes.py) if you have Python installed
    — this script is the no-dependency alternative for Windows-only setups.

.NOTES
    Schedule with Windows Task Scheduler:
      Trigger 1: At log on
      Trigger 2: On a schedule – repeat every 30 minutes indefinitely
      Action   : Start a program
                 Program : powershell.exe
                 Arguments: -NonInteractive -ExecutionPolicy Bypass -File "C:\path\to\pull_notes.ps1"
#>

# ─── Configuration ─────────────────────────────────────────────────────────────
# Edit these three values. No other changes needed.

$SERVER_URL  = "https://YOUR_ORACLE_SERVER:8000"   # No trailing slash
$AUTH_TOKEN  = "CHANGE_ME_TO_A_LONG_RANDOM_STRING"
$INBOX_PATH  = "C:\Users\YOUR_USERNAME\Documents\ObsidianVault\Inbox"

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

# ─── Guard: validate config ────────────────────────────────────────────────────
Write-Log INFO "─── Obsidian Web Clipper – Pull Run ($((Get-Date -Format 'yyyy-MM-dd HH:mm') )) ───"

if ($SERVER_URL -like "*YOUR_ORACLE_SERVER*" -or $AUTH_TOKEN -like "*CHANGE_ME*") {
    Write-Log ERROR "Please edit the configuration at the top of pull_notes.ps1 before running."
    exit 1
}

# ─── Ensure Inbox folder exists ───────────────────────────────────────────────
if (-not (Test-Path $INBOX_PATH)) {
    Write-Log WARN "Inbox not found. Creating: $INBOX_PATH"
    New-Item -ItemType Directory -Path $INBOX_PATH -Force | Out-Null
}

# ─── Fetch notes from server ──────────────────────────────────────────────────
$endpoint = "$($SERVER_URL.TrimEnd('/'))/pull-notes"
Write-Log INFO "Connecting to $endpoint …"

try {
    $headers = @{ "X-Auth-Token" = $AUTH_TOKEN }
    $response = Invoke-RestMethod `
        -Uri        $endpoint `
        -Method     GET `
        -Headers    $headers `
        -TimeoutSec 30 `
        -ErrorAction Stop
}
catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 401) {
        Write-Log ERROR "Authentication failed – check AUTH_TOKEN."
    } elseif ($null -ne $statusCode) {
        Write-Log ERROR "Server returned HTTP $statusCode"
    } else {
        Write-Log ERROR "Connection failed: $($_.Exception.Message)"
    }
    exit 1
}

# ─── Write notes to disk ──────────────────────────────────────────────────────
$count = $response.count
if ($count -eq 0) {
    Write-Log INFO "No pending notes in queue."
    exit 0
}

Write-Log INFO "Received $count note(s). Writing to: $INBOX_PATH"

$saved = 0
foreach ($note in $response.notes) {
    try {
        # Sanitize filename (remove chars illegal on Windows)
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

        Write-Log SUCCESS "  ✓ Saved: $([System.IO.Path]::GetFileName($targetPath))"
        $saved++
    }
    catch {
        Write-Log ERROR "  ✗ Failed to write '$($note.filename)': $($_.Exception.Message)"
    }
}

Write-Log INFO "Done. $saved new note(s) added to your Obsidian Inbox."
