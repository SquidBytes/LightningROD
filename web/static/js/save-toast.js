/* Phase 35 — Settings save toast auto-dismiss + row-highlight clear.
   Registers global handlers:
     * DOMContentLoaded + htmx:afterSwap → schedule removal of any
       [data-auto-dismiss] element after its ms value (default 3000).
     * click + submit at document level → strip ring-* classes from
       any element bearing them whose subtree does NOT contain the
       event target.
     * htmx:afterSwap → strip rings on elements NOT contained in the
       freshly-swapped target (server-rendered rings on the saved row
       survive the swap that landed them).
   No external dependencies. IIFE-wrapped. */
(function () {
  var RING_CLASSES = ['ring-2', 'ring-success', 'ring-offset-2', 'ring-offset-base-100'];

  function scheduleDismiss(root) {
    var scope = root || document;
    scope.querySelectorAll('[data-auto-dismiss]').forEach(function (el) {
      if (el.dataset.dismissing === '1') return;
      el.dataset.dismissing = '1';
      var ms = parseInt(el.dataset.autoDismiss, 10);
      if (isNaN(ms) || ms <= 0) ms = 3000;
      setTimeout(function () {
        if (el && el.parentNode) el.parentNode.removeChild(el);
      }, ms);
    });
  }

  function clearRings(exceptContainer) {
    document.querySelectorAll('.ring-success').forEach(function (el) {
      if (exceptContainer && (exceptContainer === el || exceptContainer.contains(el) || el.contains(exceptContainer))) {
        return;
      }
      RING_CLASSES.forEach(function (c) { el.classList.remove(c); });
    });
  }

  function onReady(fn) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn);
    } else {
      fn();
    }
  }

  onReady(function () { scheduleDismiss(document); });

  document.addEventListener('htmx:afterSwap', function (e) {
    scheduleDismiss(document);
    // The swap target's freshly-rendered content may carry a fresh ring;
    // clear rings on elements OUTSIDE the swap target so cross-region
    // saves don't leak old rings, but preserve any ring in the new content.
    var swapTarget = e && e.detail && e.detail.target;
    clearRings(swapTarget || null);
  });

  document.addEventListener('click', function (e) {
    clearRings(e.target);
  });

  document.addEventListener('submit', function () {
    clearRings(null);
  });
})();
