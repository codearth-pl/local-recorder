// Google Meet adapter.
//
// NOTE: Meet's caption DOM uses obfuscated, frequently-changing class names.
// The selectors below are best-effort and listed as comma-separated fallbacks;
// if captions stop being captured, update them (inspect a live caption element).
// Reference implementations to crib selectors from:
//   github.com/recallai/chrome-recording-transcription-extension
//   github.com/yunho0130/google-meet-cc-to-srt
(function () {
  const LR = window.__LR;
  if (!LR) return;

  function isInMeeting() {
    // "Leave call" control only exists once you're admitted to the call.
    return !!document.querySelector(
      '[aria-label*="Leave call" i], [data-tooltip*="Leave call" i], button[jsname="CQylAd"]'
    );
  }

  function getTitle() {
    const t = (document.title || "").replace(/^Meet\s*[-–]\s*/i, "").trim();
    return t || "Google Meet";
  }

  function getParticipants() {
    return []; // Optional enhancement: scrape the People panel.
  }

  function enableCaptions() {
    // Try the captions toggle button; only click when it's currently off.
    const btn = document.querySelector(
      'button[aria-label*="Turn on captions" i], button[aria-label^="Turn on captions" i]'
    );
    if (btn) {
      btn.click();
      return;
    }
    // Fallback: the 'c' keyboard shortcut toggles captions.
    document.dispatchEvent(
      new KeyboardEvent("keydown", { key: "c", code: "KeyC", bubbles: true })
    );
  }

  function trackerConfig() {
    return {
      containerSelectors: [
        'div[jsname="dsyhDe"]',
        ".a4cQT",
        '[role="region"][aria-label*="aption" i]',
        'div[aria-label*="Captions" i]',
      ],
      // Each speaker turn block.
      lineSelector: ".nMcdL, [class*='nMcdL'], .TBMuR, .iTTPOb",
      // Name node inside a turn.
      speakerSelector: ".NWpY1d, [class*='zs7s8d'], .KcIKyf",
      // Caption text node inside a turn (empty -> core falls back to line text).
      textSelector: ".bh44bd, [class*='VbkSUe'], .ygicle, .iOzk7",
      fallbackSpeaker: () => "Unknown",
    };
  }

  LR.runLifecycle({
    platform: "google-meet",
    isInMeeting,
    getTitle,
    getParticipants,
    enableCaptions,
    trackerConfig,
  });
})();
