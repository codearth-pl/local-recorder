// Shared caption-tracking core for Meet and Teams content scripts.
// Content scripts from the same extension share one isolated world per frame,
// so the platform scripts (meet.js / teams.js) read this global namespace.
//
// Design: the live-caption DOM mutates a node's text in place while a person is
// talking, then adds a new node for the next speaker turn. We track each caption
// "line" node, keep updating its text, and finalize (emit) a turn once its text
// has been stable for a short window or the node disappears.

(function () {
  const LR = (window.__LR = window.__LR || {});

  const POLL_MS = 700;
  const STABLE_MS = 1500; // emit a turn after this much silence/no-change

  function sendToBackground(payload) {
    try {
      chrome.runtime.sendMessage({ __lr: true, payload });
    } catch (e) {
      console.warn("[local-recorder] sendMessage failed", e);
    }
  }

  function firstMatch(selectors) {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  // Pulls the visible text out of an element, collapsing whitespace.
  function cleanText(el) {
    return (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
  }

  class CaptionTracker {
    // cfg: {
    //   platform, meetingId,
    //   containerSelectors: string[],
    //   lineSelector: string,                 // each speaker turn within container
    //   speakerSelector: string,              // name node within a line (optional)
    //   textSelector: string,                 // text node within a line (optional)
    //   fallbackSpeaker: () => string         // when no name node found
    // }
    constructor(cfg) {
      this.cfg = cfg;
      this.lines = new Map(); // DOM node -> { speaker, text, tStart, tEnd, sent }
      this.observer = null;
      this.poller = null;
    }

    start() {
      this.poller = setInterval(() => this.scan(), POLL_MS);
      this.scan();
    }

    stop() {
      if (this.observer) this.observer.disconnect();
      if (this.poller) clearInterval(this.poller);
      this.flushAll();
    }

    parseLine(node) {
      let speaker = "";
      if (this.cfg.speakerSelector) {
        const sEl = node.querySelector(this.cfg.speakerSelector);
        if (sEl) speaker = cleanText(sEl);
      }
      let text;
      if (this.cfg.textSelector) {
        const tEl = node.querySelector(this.cfg.textSelector);
        text = tEl ? cleanText(tEl) : "";
      } else {
        // No dedicated text node: strip the speaker prefix from the line text.
        text = cleanText(node);
        if (speaker && text.startsWith(speaker)) {
          text = text.slice(speaker.length).replace(/^[:\s]+/, "");
        }
      }
      if (!speaker) {
        speaker = (this.cfg.fallbackSpeaker && this.cfg.fallbackSpeaker()) || "Unknown";
      }
      return { speaker, text };
    }

    scan() {
      const container = firstMatch(this.cfg.containerSelectors);
      if (!container) return;
      const now = Date.now();
      const nodes = container.querySelectorAll(this.cfg.lineSelector);
      const seen = new Set();
      nodes.forEach((node) => {
        seen.add(node);
        const { speaker, text } = this.parseLine(node);
        if (!text) return;
        let rec = this.lines.get(node);
        if (!rec) {
          rec = { speaker, text, tStart: now, tEnd: now, sent: false };
          this.lines.set(node, rec);
        } else if (text !== rec.text || speaker !== rec.speaker) {
          rec.text = text;
          rec.speaker = speaker;
          rec.tEnd = now;
          rec.sent = false; // text grew; re-emit final version later
        }
      });

      // Emit lines that are stable (unchanged for STABLE_MS) or gone from the DOM.
      for (const [node, rec] of this.lines.entries()) {
        const gone = !seen.has(node);
        const stable = now - rec.tEnd > STABLE_MS;
        if (!rec.sent && (gone || stable)) {
          this.emit(rec);
          rec.sent = true;
        }
        if (gone) this.lines.delete(node);
      }
    }

    emit(rec) {
      sendToBackground({
        type: "caption",
        id: this.cfg.meetingId,
        platform: this.cfg.platform,
        speaker: rec.speaker,
        text: rec.text,
        t_start: rec.tStart,
        t_end: rec.tEnd,
      });
    }

    flushAll() {
      for (const rec of this.lines.values()) {
        if (!rec.sent && rec.text) this.emit(rec);
      }
      this.lines.clear();
    }
  }

  LR.CaptionTracker = CaptionTracker;
  LR.sendToBackground = sendToBackground;
  LR.firstMatch = firstMatch;
  LR.cleanText = cleanText;

  // Generic SPA meeting-lifecycle manager used by both platforms.
  // platformCfg: {
  //   platform, isInMeeting: () => bool, getTitle: () => string,
  //   getParticipants: () => string[], enableCaptions: () => void,
  //   trackerConfig: () => object  // selectors etc. (without meetingId)
  // }
  LR.runLifecycle = function (platformCfg) {
    let meetingId = null;
    let tracker = null;

    function startMeeting() {
      meetingId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
      sendToBackground({
        type: "meeting_start",
        id: meetingId,
        platform: platformCfg.platform,
        title: platformCfg.getTitle(),
        url: location.href,
        participants: platformCfg.getParticipants(),
      });
      try {
        platformCfg.enableCaptions();
      } catch (e) {
        console.warn("[local-recorder] enableCaptions failed", e);
      }
      tracker = new LR.CaptionTracker({
        ...platformCfg.trackerConfig(),
        platform: platformCfg.platform,
        meetingId,
      });
      tracker.start();
      console.log("[local-recorder] meeting started", meetingId);
    }

    function stopMeeting() {
      if (tracker) {
        tracker.stop();
        tracker = null;
      }
      if (meetingId) {
        sendToBackground({ type: "meeting_stop", id: meetingId });
        console.log("[local-recorder] meeting stopped", meetingId);
        meetingId = null;
      }
    }

    // Poll the SPA for join/leave transitions.
    setInterval(() => {
      const inMeeting = platformCfg.isInMeeting();
      if (inMeeting && !meetingId) startMeeting();
      else if (!inMeeting && meetingId) stopMeeting();
    }, 2000);

    window.addEventListener("beforeunload", stopMeeting);
  };
})();
