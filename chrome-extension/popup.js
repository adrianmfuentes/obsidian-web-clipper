/**
 * popup.js
 * Handles all UI interactions for the Obsidian Web Clipper popup.
 *
 * Flow:
 *  1. On load → read current tab info + check saved settings
 *  2. "Clip" button → inject content.js → POST to /capture endpoint
 *  3. Settings gear → save/load server URL and auth token
 */

'use strict';

// ─── DOM refs ─────────────────────────────────────────────────────────────────
const viewMain        = document.getElementById('view-main');
const viewSettings    = document.getElementById('view-settings');
const btnToggleView   = document.getElementById('btn-toggle-view');

const pageTitle       = document.getElementById('page-title');
const pageUrl         = document.getElementById('page-url');
const statusMain      = document.getElementById('status-main');

const btnClip         = document.getElementById('btn-clip');
const btnOpenSettings = document.getElementById('btn-open-settings-from-main');

const statusSettings  = document.getElementById('status-settings');
const inputServerUrl  = document.getElementById('input-server-url');
const inputToken      = document.getElementById('input-token');
const btnSaveSettings = document.getElementById('btn-save-settings');
const btnCancelSettings = document.getElementById('btn-cancel-settings');

// ─── State ─────────────────────────────────────────────────────────────────────
let currentTab = null;
let savedSettings = { serverUrl: '', token: '' };

// ─── Init ──────────────────────────────────────────────────────────────────────
(async function init() {
  // Load settings from chrome.storage
  const stored = await chrome.storage.local.get(['serverUrl', 'token']);
  savedSettings.serverUrl = stored.serverUrl || '';
  savedSettings.token     = stored.token     || '';

  // Fill settings inputs
  inputServerUrl.value = savedSettings.serverUrl;
  inputToken.value     = savedSettings.token;

  // Get the active tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentTab = tab;

  pageTitle.textContent = tab.title || 'Unknown page';
  pageUrl.textContent   = tab.url   || '';

  // Warn if server is not yet configured
  if (!savedSettings.serverUrl || !savedSettings.token) {
    showStatus(statusMain, 'warning', 'Server URL or token not set. Configure in settings first.');
    btnOpenSettings.style.display = 'block';
    btnClip.disabled = true;
  }
})();

// ─── View toggle (main ↔ settings) ────────────────────────────────────────────
btnToggleView.addEventListener('click', () => toggleView());
btnCancelSettings.addEventListener('click', () => toggleView(false));
btnOpenSettings.addEventListener('click', () => toggleView(true));

function toggleView(forceSettings) {
  const goToSettings = forceSettings ?? viewMain.classList.contains('active');
  viewMain.classList.toggle('active', !goToSettings);
  viewSettings.classList.toggle('active', goToSettings);
  btnToggleView.textContent = goToSettings ? '✕' : '⚙';
  clearStatus(statusSettings);
}

// ─── Save settings ────────────────────────────────────────────────────────────
btnSaveSettings.addEventListener('click', async () => {
  const url   = inputServerUrl.value.trim().replace(/\/$/, ''); // strip trailing slash
  const token = inputToken.value.trim();

  if (!url) {
    showStatus(statusSettings, 'error', 'Server URL cannot be empty.');
    return;
  }
  if (!token) {
    showStatus(statusSettings, 'error', 'Security token cannot be empty.');
    return;
  }

  await chrome.storage.local.set({ serverUrl: url, token });
  savedSettings = { serverUrl: url, token };

  showStatus(statusSettings, 'success', 'Settings saved!');

  // Re-enable clip button on main view
  btnClip.disabled = false;
  btnOpenSettings.style.display = 'none';
  clearStatus(statusMain);

  setTimeout(() => toggleView(false), 800);
});

// ─── Clip article ─────────────────────────────────────────────────────────────
btnClip.addEventListener('click', async () => {
  btnClip.disabled  = true;
  btnClip.textContent = '⏳ Extracting…';
  clearStatus(statusMain);

  try {
    // Step 1 – inject content.js into the active tab to extract article text
    const results = await chrome.scripting.executeScript({
      target: { tabId: currentTab.id },
      files: ['content.js'],
    });

    const extracted = results?.[0]?.result;
    if (!extracted || !extracted.text) {
      throw new Error('Could not extract content from this page.');
    }

    // Step 2 – POST to the Oracle server /capture endpoint
    btnClip.textContent = '☁ Sending to server…';

    const endpoint = `${savedSettings.serverUrl}/capture`;
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Auth-Token': savedSettings.token,
      },
      body: JSON.stringify({
        title: extracted.title,
        url:   extracted.url,
        text:  extracted.text,
      }),
    });

    if (!response.ok) {
      const body = await response.text();
      throw new Error(`Server responded ${response.status}: ${body}`);
    }

    showStatus(statusMain, 'success', '✓ Article queued! Gemini will process it shortly.');
    btnClip.textContent = '✦ Clip this Article';

  } catch (err) {
    console.error('[Obsidian Clipper]', err);
    showStatus(statusMain, 'error', `Error: ${err.message}`);
    btnClip.textContent = '✦ Clip this Article';
  } finally {
    btnClip.disabled = false;
  }
});

// ─── Helpers ──────────────────────────────────────────────────────────────────
function showStatus(el, type, message) {
  el.textContent = message;
  el.className = `status show ${type}`;
}

function clearStatus(el) {
  el.className = 'status';
  el.textContent = '';
}
