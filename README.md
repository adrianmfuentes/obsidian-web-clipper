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

> Placeholder icons ship in `chrome-extension/icons/` so **Load unpacked** works
> out of the box. Swap `icon16.png` / `icon48.png` / `icon128.png` for your own
> artwork any time — just keep the same filenames and pixel sizes.

---

#### Optional: publish to the Chrome Web Store

By default, tagging a release (`git tag v1.2.3 && git push --tags`) only creates
a GitHub Release with a zip (see [CI/CD](#cicd)) — installing still means
**Load unpacked**. To have `extension-release.yml` also push straight to the
Chrome Web Store on every tag, set these in the repo's Settings:

- **Settings → Secrets and variables → Actions → Variables**: `CHROME_EXTENSION_ID`
- **Settings → Secrets and variables → Actions → Secrets**: `CHROME_CLIENT_ID`,
  `CHROME_CLIENT_SECRET`, `CHROME_REFRESH_TOKEN` (from a Google Cloud OAuth client
  with access to the Chrome Web Store API — see
  [chrome-extension-upload](https://github.com/mnao305/chrome-extension-upload)
  for how to generate them)

Leave `CHROME_EXTENSION_ID` unset and the publish step is skipped automatically —
the GitHub Release zip still gets created either way.

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

A note is only removed from the server's queue once it's been written to
disk successfully (`/pull-notes` -> write -> `/ack-notes`), so a crash
mid-run just means it gets fetched again next time — nothing is lost.

---

### 5 · Get notes into your vault: scheduled pulls or watch mode

Two ways to run the local client — pick one:

**Option A — Scheduled one-shot pulls** (`pull_notes.py` / `pull_notes.ps1`
with no arguments): runs once, exits. Good for Task Scheduler / cron on a
fixed interval — new clips show up within that interval.

**Option B — Watch mode** (`python pull_notes.py --watch` or
`pull_notes.ps1 -Watch`): a single long-running process that long-polls the
server, so new clips land in your Inbox within seconds instead of waiting for
the next scheduled run. Run it once (e.g. at login, or as a background
service) instead of scheduling repeated pulls.

**Windows Task Scheduler (one-shot):**

1. Open **Task Scheduler** → **Create Basic Task**
2. Name: `Obsidian Web Clipper Pull`
3. **Trigger**: `When I log on` (add a second trigger: `On a schedule`, repeat every 30 min)
4. **Action**: `Start a program`
   - Program: `pythonw` (not `python` — see note below)
   - Arguments: `"C:\path\to\pull_notes.py"`
   - Start in: `C:\path\to\local-client\`
5. Finish.

> **Use `pythonw`, not `python`.** `python.exe` is a console app, so Task
> Scheduler briefly flashes a terminal window into the foreground every time
> it fires — every 30 minutes, indefinitely. `pythonw.exe` is the windowless
> twin of the same interpreter (ships in the same folder) and runs fully in
> the background. Since there's then no console to print to, `pull_notes.py`
> always writes to `pull_notes.log` next to itself — check there instead.

Easier: just run `schedule_task.ps1` in this folder (as Administrator) — it
does exactly the above and auto-detects `pythonw.exe` for you:
```powershell
powershell -ExecutionPolicy Bypass -File local-client\schedule_task.ps1
```

**macOS / Linux cron (one-shot):**
```cron
*/30 * * * * /usr/bin/python3 /path/to/pull_notes.py >> /tmp/clipper.log 2>&1
```

**Watch mode (either platform), instead of the above:**
```bash
python pull_notes.py --watch      # foreground; Ctrl+C to stop
```
```powershell
.\pull_notes.ps1 -Watch
```
Run it under whatever keeps a process alive on your machine (Task Scheduler
"At log on" trigger with no repetition, a systemd user service, `screen`/`tmux`,
pm2, etc.) — the script itself runs forever until stopped. On Windows, launch
it with `pythonw` too (`pythonw pull_notes.py --watch`) so it runs with no
visible console window at all instead of one sitting open in the background.

---

## API Reference

| Method | Path          | Auth | Description                                                             |
|--------|---------------|------|--------------------------------------------------------------------------|
| GET    | `/health`     | No   | Liveness check                                                          |
| POST   | `/capture`    | Yes  | Receive article, call Gemini, queue result. Repeat calls with the same URL+text are deduped — no extra Gemini spend, no duplicate notes. |
| GET    | `/pull-notes` | Yes  | Return pending notes. **Non-destructive** — call `/ack-notes` once they're safely written to disk. |
| POST   | `/ack-notes`  | Yes  | Delete previously-pulled notes by id.                                   |
| GET    | `/wait-notes` | Yes  | Long-poll: returns immediately if notes are already pending, otherwise blocks (up to `?timeout=` seconds, default 25, capped at 55) until a new one is queued. Same response shape as `/pull-notes`. |

**Auth header:** `X-Auth-Token: <your-token>`

**POST /capture body:**
```json
{
  "title": "Article Title",
  "url":   "https://example.com/article",
  "text":  "Full cleaned article text…"
}
```
Response is `{"status": "queued", "filename": "..."}` or, for a repeat of an
already-queued capture, `{"status": "duplicate", "filename": "..."}`.

**GET /pull-notes (and /wait-notes) response:**
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

**POST /ack-notes body:**
```json
{ "ids": [1, 2] }
```
Response: `{"deleted": 2}`.

---

## CI/CD

Three GitHub Actions workflows run automatically:

| Workflow                    | Trigger                                    | What it does                                                        |
|------------------------------|---------------------------------------------|-----------------------------------------------------------------------|
| `ci.yml`                     | Push / PR to `master`                       | Lints + tests the server (`ruff`, `pytest`), validates the extension's `manifest.json` and JS syntax, and parse-checks the PowerShell scripts. |
| `docker-publish.yml`         | Push to `master` touching `server/**`, or a `v*` tag | Builds the multi-arch (amd64/arm64) server image and publishes it to `ghcr.io/adrianmfuentes/obsidian-clipper-server`. |
| `extension-release.yml`      | Push of a `v*` tag                          | Fails fast if `manifest.json`'s version doesn't match the tag, then zips `chrome-extension/` and attaches it to a GitHub Release. Also publishes to the Chrome Web Store if configured (see [above](#optional-publish-to-the-chrome-web-store)). |

`server/tests/` holds the pytest suite (`test_database.py`, `test_main.py`) — run locally with:
```bash
cd server
pip install -r requirements.txt pytest ruff
pytest tests -v
ruff check . --line-length 120 --select E4,E7,E9,F
```

---

## Security Notes

- Never commit your `.env` file or expose `AUTH_TOKEN` publicly.
- Always put the server behind HTTPS (Caddy/Nginx + Let's Encrypt).
- The Chrome extension stores the token in `chrome.storage.local` (encrypted by Chrome).
- The server runs as a non-root user inside the container.
