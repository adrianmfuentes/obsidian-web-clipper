# Obsidian Web Clipper

Async web-to-Obsidian pipeline:
**Chrome Extension** → **Oracle ARM64 Server** (FastAPI + Gemini) → **Local Obsidian Vault**

```
[Browser] ──POST /capture──► [Oracle Server Queue] ──GET /pull-notes──► [Obsidian Inbox]
```

---

## Project Structure

```
obsidian-web-clipper/
├── chrome-extension/
│   ├── manifest.json       MV3 manifest
│   ├── popup.html          Extension UI
│   ├── popup.js            UI logic + fetch to server
│   ├── content.js          Article text extractor (injected into tab)
│   └── background.js       MV3 service worker (minimal)
│
├── server/
│   ├── main.py             FastAPI app (capture + pull-notes endpoints)
│   ├── database.py         SQLite queue setup
│   ├── requirements.txt
│   ├── Dockerfile          ARM64-optimized multi-stage build
│   ├── docker-compose.yml  Portainer-ready stack
│   └── .env.example        Environment variable template
│
└── local-client/
    ├── pull_notes.py       Python downloader (cross-platform)
    └── pull_notes.ps1      PowerShell downloader (Windows, no extra deps)
```

---

## Step-by-Step Setup

### 1 · Get a Gemini API Key

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Create a new key — the free tier is sufficient for personal use.

---

### 2 · Deploy the Server (Oracle ARM64)

The server image is built and published automatically by
[`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml)
to `ghcr.io/adrianmfuentes/obsidian-clipper-server` (multi-arch: amd64 + arm64)
on every push to `master` that touches `server/**`. No manual build step is
needed on the Oracle server anymore — just pull.

**Option A – Portainer Stack (recommended)**

1. SSH into your Oracle server, create the data directory:
   ```bash
   mkdir -p /opt/obsidian-clipper/data
   ```
2. In Portainer → **Stacks → Add Stack**, paste the contents of `docker-compose.yml`.
3. Replace the two `CHANGE_ME` values:
   - `AUTH_TOKEN` — generate one: `openssl rand -hex 32`
   - `GEMINI_API_KEY` — from AI Studio
4. Deploy the stack (Portainer will pull the published image from GHCR).

**Option B – docker-compose directly**

```bash
# On your Oracle server
git clone <this-repo> obsidian-clipper
cd obsidian-clipper/server
cp .env.example .env
nano .env              # fill in AUTH_TOKEN and GEMINI_API_KEY
docker compose pull    # fetch the latest published image
docker compose up -d
```

**Verify it works:**
```bash
curl http://localhost:8000/health
# {"status":"ok","model":"gemini-1.5-flash"}
```

**Expose it to the internet** (use HTTPS!):
- Point a subdomain at your server IP (e.g. `clipper.yourdomain.com`).
- Put Nginx/Caddy in front with a Let's Encrypt certificate.
- Caddy one-liner example:
  ```
  clipper.yourdomain.com {
      reverse_proxy localhost:8000
  }
  ```

---

### 3 · Install the Chrome Extension

1. Open Chrome → `chrome://extensions/` → Enable **Developer mode** (top right).
2. Click **Load unpacked** → select the `chrome-extension/` folder.
3. Click the extension icon → click the ⚙ gear icon.
4. Enter:
   - **Server URL**: `https://clipper.yourdomain.com` (or `http://YOUR_SERVER_IP:8000`)
   - **Security Token**: the same `AUTH_TOKEN` from your `.env`
5. Click **Save**.

> **Icons**: The `icons/` folder is referenced in `manifest.json` but no PNG files are
> included here. Drop any 16×16, 48×48, and 128×128 PNG images named `icon16.png`,
> `icon48.png`, `icon128.png` into `chrome-extension/icons/`. You can use any simple
> icon or generate one at [favicon.io](https://favicon.io).

---

### 4 · Configure the Local Client

**Edit `local-client/pull_notes.py`** (or `pull_notes.ps1`):

```python
CONFIG = {
    "server_url": "https://clipper.yourdomain.com",
    "auth_token":  "your-auth-token-here",
    "inbox_path":  r"C:\Users\YourName\Documents\MyVault\Inbox",
}
```

**Install dependency:**
```bash
pip install requests
```

**Test it manually:**
```bash
python pull_notes.py
```

---

### 5 · Schedule the Local Client (Windows)

**Windows Task Scheduler:**

1. Open **Task Scheduler** → **Create Basic Task**
2. Name: `Obsidian Web Clipper Pull`
3. **Trigger**: `When I log on` (add a second trigger: `On a schedule`, repeat every 30 min)
4. **Action**: `Start a program`
   - Program: `python`
   - Arguments: `"C:\path\to\pull_notes.py"`
   - Start in: `C:\path\to\local-client\`
5. Finish.

Or import via PowerShell (run as Administrator):
```powershell
$action  = New-ScheduledTaskAction -Execute "python" -Argument "C:\path\to\pull_notes.py"
$trigger = @(
    New-ScheduledTaskTrigger -AtLogOn,
    New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 30) -Once -At (Get-Date)
)
Register-ScheduledTask -TaskName "Obsidian Clipper Pull" -Action $action -Trigger $trigger -RunLevel Highest
```

**macOS / Linux cron:**
```cron
*/30 * * * * /usr/bin/python3 /path/to/pull_notes.py >> /tmp/clipper.log 2>&1
```

---

## API Reference

| Method | Path          | Auth | Description                                              |
|--------|---------------|------|----------------------------------------------------------|
| GET    | `/health`     | No   | Liveness check                                           |
| POST   | `/capture`    | Yes  | Receive article, call Gemini, queue result               |
| GET    | `/pull-notes` | Yes  | Return + delete all pending notes (destructive read)     |

**Auth header:** `X-Auth-Token: <your-token>`

**POST /capture body:**
```json
{
  "title": "Article Title",
  "url":   "https://example.com/article",
  "text":  "Full cleaned article text…"
}
```

**GET /pull-notes response:**
```json
{
  "count": 2,
  "notes": [
    {
      "id": 1,
      "title": "Article Title",
      "url": "https://example.com/article",
      "markdown": "---\ntitle: ...\n---\n## Summary\n...",
      "created_at": "2026-03-30 14:22:01",
      "filename": "20260330-142201-Article-Title.md"
    }
  ]
}
```

---

## CI/CD

Three GitHub Actions workflows run automatically:

| Workflow                    | Trigger                                    | What it does                                                        |
|------------------------------|---------------------------------------------|-----------------------------------------------------------------------|
| `ci.yml`                     | Push / PR to `master`                       | Lints + tests the server (`ruff`, `pytest`), validates the extension's `manifest.json` and JS syntax, and parse-checks the PowerShell scripts. |
| `docker-publish.yml`         | Push to `master` touching `server/**`, or a `v*` tag | Builds the multi-arch (amd64/arm64) server image and publishes it to `ghcr.io/adrianmfuentes/obsidian-clipper-server`. |
| `extension-release.yml`      | Push of a `v*` tag                          | Zips `chrome-extension/` and attaches it to a GitHub Release.       |

`server/tests/` holds the pytest suite (`test_database.py`, `test_main.py`) — run locally with:
```bash
cd server
pip install -r requirements.txt pytest ruff
pytest tests -v
ruff check . --line-length 120
```

---

## Security Notes

- Never commit your `.env` file or expose `AUTH_TOKEN` publicly.
- Always put the server behind HTTPS (Caddy/Nginx + Let's Encrypt).
- The Chrome extension stores the token in `chrome.storage.local` (encrypted by Chrome).
- The server runs as a non-root user inside the container.
