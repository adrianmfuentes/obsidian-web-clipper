/**
 * background.js  –  MV3 Service Worker
 *
 * Kept minimal on purpose. The popup handles everything directly.
 * This service worker exists to satisfy MV3 requirements and to
 * provide a clean place for future event-based features (e.g.,
 * context-menu clipping, badge updates).
 */

chrome.runtime.onInstalled.addListener(() => {
  console.log('[Obsidian Clipper] Extension installed / updated.');
});
