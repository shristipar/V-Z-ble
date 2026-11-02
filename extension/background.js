// Posts the profile to the V-Z-ble site you last opened (paired by pair.js). This runs in the
// extension's service worker, which may call those sites (host_permissions) without LinkedIn's
// page restrictions.
//
// Only these sites can receive your LinkedIn data. Keep in sync with manifest.json.
// Add a custom domain here and in manifest.json if you set one up.
const APP_ORIGINS = ["http://localhost:8765", "https://v-z-ble-qukdgtc2ca-uc.a.run.app"];

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "import") return;
  chrome.storage.local.get(["server", "uid"]).then(({ server, uid }) => {
    if (!APP_ORIGINS.includes(server) || !uid) {
      return sendResponse({ ok: false, needsPairing: true });
    }
    return fetch(`${server}/api/linkedin`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-VZB-User": uid },
      body: JSON.stringify(msg.profile),
    })
      .then(async r => sendResponse({ ok: r.ok, status: r.status, server, body: await r.json().catch(() => ({})) }))
      .catch(e => sendResponse({ ok: false, server, error: String(e) }));
  });
  return true; // keep the channel open for the async response
});
