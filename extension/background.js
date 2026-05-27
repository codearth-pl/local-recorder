// Service worker: relays content-script messages to the native messaging host.
// MV3 service workers can be evicted; we (re)connect the native port lazily on
// each message and on demand, so the daemon connection is resilient to restarts.

const HOST_NAME = "com.localrecorder.host";
let port = null;

function connect() {
  if (port) return port;
  try {
    port = chrome.runtime.connectNative(HOST_NAME);
    port.onDisconnect.addListener(() => {
      const err = chrome.runtime.lastError;
      console.warn("[local-recorder] native port disconnected", err && err.message);
      port = null;
    });
    port.onMessage.addListener((msg) => {
      // Host only sends acks/errors; surface them for debugging.
      if (msg && msg.ok === false) {
        console.error("[local-recorder] host error:", msg.error);
      }
    });
  } catch (e) {
    console.error("[local-recorder] connectNative failed", e);
    port = null;
  }
  return port;
}

function relay(msg) {
  const p = connect();
  if (!p) return false;
  try {
    p.postMessage(msg);
    return true;
  } catch (e) {
    console.warn("[local-recorder] postMessage failed, reconnecting", e);
    port = null;
    const p2 = connect();
    if (!p2) return false;
    p2.postMessage(msg);
    return true;
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.__lr) {
    const ok = relay(msg.payload);
    sendResponse({ ok });
  }
  return false; // synchronous response
});
