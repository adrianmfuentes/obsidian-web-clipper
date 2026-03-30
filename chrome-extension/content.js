/**
 * content.js
 * Injected into the active tab by popup.js via chrome.scripting.executeScript.
 * Extracts the cleanest possible article text from the current page.
 */

function extractArticleContent() {
  // --- 1. Try semantic / well-known article selectors in priority order ---
  const candidateSelectors = [
    'article',
    '[role="main"]',
    '.post-content',
    '.article-content',
    '.article-body',
    '.entry-content',
    '.post-body',
    '.story-body',
    '#article-body',
    '#main-content',
    'main',
  ];

  let contentElement = null;
  for (const selector of candidateSelectors) {
    const el = document.querySelector(selector);
    if (el && el.innerText && el.innerText.trim().length > 200) {
      contentElement = el;
      break;
    }
  }

  // Fallback: use full body if no article container was found
  if (!contentElement) {
    contentElement = document.body;
  }

  // --- 2. Clone the node so we can remove noise without touching the live DOM ---
  const clone = contentElement.cloneNode(true);

  // Remove elements that never contain article text
  const noiseSelectors = [
    'script', 'style', 'noscript',
    'nav', 'header', 'footer', 'aside',
    '.sidebar', '.navigation', '.menu',
    '.ad', '.ads', '.advertisement', '.banner',
    '.cookie-notice', '.popup', '.modal',
    '.related-posts', '.recommended',
    '[aria-hidden="true"]',
  ];

  noiseSelectors.forEach(sel => {
    clone.querySelectorAll(sel).forEach(el => el.remove());
  });

  // --- 3. Extract and clean the text ---
  let rawText = clone.innerText || clone.textContent || '';

  // Collapse excessive whitespace while preserving paragraph breaks
  const cleanText = rawText
    .replace(/\t/g, ' ')           // tabs → spaces
    .replace(/ {2,}/g, ' ')        // multiple spaces → one
    .replace(/\n{3,}/g, '\n\n')    // more than 2 newlines → 2
    .trim()
    .substring(0, 15000);          // Gemini context limit safety cap

  return {
    title: document.title.trim(),
    url: window.location.href,
    text: cleanText,
  };
}

// Return value is forwarded back to popup.js by the scripting API
extractArticleContent();
