// Adds a "Send to V-Z-ble" button on LinkedIn profile pages (/in/...). LinkedIn is a
// single-page app, so the script runs on every LinkedIn page and shows the button only on profiles.
(() => {
  const btn = document.createElement("button");
  btn.textContent = "Send to V-Z-ble";
  Object.assign(btn.style, {
    position: "fixed", right: "20px", bottom: "20px", zIndex: 99999, padding: "10px 16px",
    font: "600 14px system-ui, sans-serif", color: "#fff", background: "#1f6f5c",
    border: "0", borderRadius: "8px", boxShadow: "0 4px 14px rgba(0,0,0,.25)", cursor: "pointer",
  });
  let confirmOther = false;

  const label = (text, ms) => {
    btn.textContent = text;
    if (ms) setTimeout(() => { btn.textContent = "Send to V-Z-ble"; btn.disabled = false; confirmOther = false; }, ms);
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // LinkedIn loads lower sections (Experience, Education, Skills) only as they scroll into view.
  // Newer layouts scroll an inner panel rather than the window, so scroll whichever element
  // actually scrolls, and keep going while the page grows.
  function scroller() {
    const candidates = [document.scrollingElement, ...document.querySelectorAll("div, main, section")]
      .filter(el => el && el.scrollHeight > el.clientHeight + 200
        && (el === document.scrollingElement || /auto|scroll/.test(getComputedStyle(el).overflowY)));
    return candidates.sort((a, b) => b.scrollHeight - a.scrollHeight)[0] || document.scrollingElement;
  }

  async function loadAllSections() {
    const el = scroller();
    const start = el.scrollTop;
    let height = 0;
    for (let i = 0; i < 25 && el.scrollHeight !== height; i++) {
      height = el.scrollHeight;
      for (let step = 1; step <= 4; step++) { el.scrollTop = height * step / 4; await sleep(300); }
      await sleep(500);
    }
    el.scrollTop = start;
    await sleep(300);
  }

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    label("Reading profile…");
    await loadAllSections();
    const cutOffCount = await expandTruncatedText();
    const profile = extractLinkedInProfile();
    profile.cutOffCount = cutOffCount;
    // Lets the app spot an outdated extension (Chrome keeps old code until you reload it).
    profile.extensionVersion = chrome.runtime.getManifest().version;
    if (!profile.looksLikeOwnProfile && !confirmOther) {
      confirmOther = true; btn.disabled = false;
      label("This may not be your profile. Click again to send anyway.");
      return;
    }
    chrome.runtime.sendMessage({ type: "import", profile }, res => {
      if (chrome.runtime.lastError || !res) return label("Couldn't reach the extension. Reload this page.", 4000);
      if (res.needsPairing) return label("Open V-Z-ble in a tab first, then send again.", 5000);
      if (res.ok) return label("Sent. Check your V-Z-ble tab.", 3000);
      label(res.error ? `Couldn't reach ${new URL(res.server).host}. Is V-Z-ble running?` : `Server error ${res.status}`, 5000);
    });
  });

  const sync = () => {
    const onProfile = /^\/in\/[^/]+\/?$/.test(location.pathname);
    if (onProfile && !btn.isConnected) document.body.appendChild(btn);
    if (!onProfile && btn.isConnected) btn.remove();
  };
  sync();
  setInterval(sync, 1000);
})();
