// Perceptify service worker: talks to the local backend and handles the toolbar icon.
const BACKEND = "http://127.0.0.1:8000";

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type !== "analyze") return;
  const url = `${BACKEND}/analyze?video_id=${encodeURIComponent(msg.videoId)}${msg.refresh ? "&refresh=1" : ""}`;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 120000);
  fetch(url, { signal: ctl.signal })
    .then(async (res) => {
      const data = await res.json().catch(() => ({}));
      reply(res.ok ? { ok: true, data } : { ok: false, error: data.error || `Backend error ${res.status}` });
    })
    .catch((e) =>
      reply({
        ok: false,
        error: e.name === "AbortError"
          ? "That took too long. Press Retry."
          : "Can't reach the Perceptify backend. Start it with ./run.sh in the backend folder.",
      })
    )
    .finally(() => clearTimeout(timer));
  return true; // keep the channel open for the async reply
});

// Clicking the toolbar icon opens the panel on the current YouTube tab.
chrome.action.onClicked.addListener((tab) => {
  if (!tab.id) return;
  chrome.tabs.sendMessage(tab.id, { type: "toggle" }).catch(() => {});
});
