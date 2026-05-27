// Microsoft Teams (web) adapter.
//
// NOTE: like Meet, Teams caption markup changes over time. Selectors are
// best-effort, comma-separated fallbacks; update by inspecting a live caption.
// Teams uses data-tid attributes which tend to be more stable than CSS classes.
(function () {
  const LR = window.__LR;
  if (!LR) return;

  function isInMeeting() {
    return !!document.querySelector(
      '#hangup-button, [data-tid="hangup-button"], [data-tid="call-end"], button[aria-label*="Leave" i]'
    );
  }

  function getTitle() {
    const t = (document.title || "").replace(/\s*[|–-]\s*Microsoft Teams.*$/i, "").trim();
    return t || "Microsoft Teams";
  }

  function getParticipants() {
    return [];
  }

  function enableCaptions() {
    // Teams buries live captions under the "More" menu; auto-enable is
    // unreliable. Best-effort: click a captions toggle if one is exposed.
    const btn = document.querySelector(
      '[data-tid="closed-captions-button"], button[aria-label*="captions" i]'
    );
    if (btn) btn.click();
    // Otherwise the user enables captions via More (...) -> Language and speech.
  }

  function trackerConfig() {
    return {
      containerSelectors: [
        '[data-tid="closed-caption-renderer-wrapper"]',
        '[data-tid="closed-captions-renderer"]',
        '[data-tid="closed-caption-v2-window"]',
      ],
      lineSelector:
        '[data-tid="closed-caption-message"], .ui-chat__item, .fui-ChatMessage',
      speakerSelector:
        '[data-tid="author"], .ui-chat__message__author, .fui-ChatMessage__author',
      textSelector:
        '[data-tid="closed-caption-text"], .ui-chat__message__content, .fui-ChatMessage__body',
      fallbackSpeaker: () => "Unknown",
    };
  }

  LR.runLifecycle({
    platform: "microsoft-teams",
    isInMeeting,
    getTitle,
    getParticipants,
    enableCaptions,
    trackerConfig,
  });
})();
