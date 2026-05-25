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
  window.LR = window.LR || {};
  var RING_CLASSES = ['ring-2', 'ring-success', 'ring-accent', 'ring-offset-2', 'ring-offset-base-100'];

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
    document.querySelectorAll('.ring-success, .ring-accent').forEach(function (el) {
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
    // Only sweep rings on save-like swaps. A GET swap (clicking a row to load
    // its detail into the drawer) targets a different region and would
    // otherwise strip the just-applied selection ring off the clicked row.
    var verb = e && e.detail && e.detail.requestConfig && e.detail.requestConfig.verb;
    if (verb && String(verb).toLowerCase() === 'get') return;
    var swapTarget = e && e.detail && e.detail.target;
    clearRings(swapTarget || null);
  });

  // Listen in the CAPTURE phase so the exemption check runs before any inline
  // onclick has a chance to detach the click target (e.g. Cancel buttons that
  // call `modal-content.innerHTML = ''`, which orphans the element and breaks
  // its closest()-based ancestor walk).
  document.addEventListener('click', function (e) {
    var t = e.target;
    // Skip if the click is on a dismissal affordance (overlay/backdrop/toggle/
    // explicit data-dismiss-surface) OR inside a drawer panel or modal box.
    // The latter cover in-surface interactions — Edit, Save, Cancel, Advanced
    // Edit, etc. — which are about the ringed row and shouldn't clear it.
    if (t && t.closest && t.closest('.drawer-overlay, .modal-backdrop, .drawer-toggle, .drawer-side, .modal-box, [data-dismiss-surface]')) return;
    clearRings(t);
  }, true);

  // (No submit listener: form-driven saves emit htmx:afterSwap which handles
  // ring lifecycle via clearRings(swapTarget). A document-level submit hook
  // also fired on dialog-dismiss forms like the modal close X, wiping the
  // ring on what's logically a dismissal.)

  // Public hooks for JS-created toasts (e.g. sessions/trips pages that build
  // success toasts in response to custom HX-Trigger events). Callers append a
  // [data-auto-dismiss] element to the DOM and invoke scheduleDismiss; the
  // shared timeout + dismiss-flag bookkeeping lives here so all toasts share
  // one lifecycle.
  LR.scheduleDismiss = scheduleDismiss;
  LR.clearRings = clearRings;
})();
