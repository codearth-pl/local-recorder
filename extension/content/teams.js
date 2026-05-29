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
      // Match the whole message ROW (.fui-ChatMessageCompact), not the inner
      // text wrapper ([data-tid="closed-caption-message"]) — the author node is
      // a sibling of that wrapper, so matching the wrapper strands the speaker.
      lineSelector:
        '.fui-ChatMessageCompact, [class*="fui-ChatMessageCompact"], .fui-ChatMessage, [class*="fui-ChatMessage"], .ui-chat__item',
      speakerSelector:
        '[data-tid="author"], [class*="fui-ChatMessageCompact__author"], .fui-ChatMessage__author, .ui-chat__message__author',
      textSelector:
        '[data-tid="closed-caption-text"], [data-tid="closed-caption-message"], [class*="fui-ChatMessageCompact__body"], .fui-ChatMessage__body, .ui-chat__message__content',
      // Ancestor row used by the core to find a speaker outside the line node.
      rowSelector:
        '.fui-ChatMessageCompact, [class*="fui-ChatMessageCompact"], .fui-ChatMessage, [class*="fui-ChatMessage"], .ui-chat__item',
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
