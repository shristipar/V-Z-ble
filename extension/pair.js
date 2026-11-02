// Runs on V-Z-ble's own pages (see manifest.json). Remembers this browser's visitor ID and which
// V-Z-ble site it came from, so LinkedIn imports go to your audit and nobody else's.
(() => {
  let uid = "";
  try { uid = localStorage.getItem("vzb.uid") || ""; } catch { /* storage blocked */ }
  if (!/^[a-f0-9]{32}$/.test(uid)) return;
  chrome.storage.local.set({ server: location.origin, uid });
  // Lets the page show that the extension is connected.
  document.documentElement.dataset.vzbExtension = chrome.runtime.getManifest().version;
})();
