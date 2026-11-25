/* Toasts (one container for success and error) + row-highlight clear.
   LR.toast(message, type, ms) shows a toast; server-rendered
   .toast[data-auto-dismiss] blocks are moved into the same container.
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
  var ROOT_ID = 'toast-root';
  var TOAST_CLASSES = 'toast toast-bottom toast-end z-50';

  // An open <dialog> draws over the page, so its toasts live inside it and
  // move back to the page when it closes.
  function toastRoot() {
    var page = document.getElementById(ROOT_ID);
    var dialogs = document.querySelectorAll('dialog[open]');
    if (!dialogs.length) return page;
    var dialog = dialogs[dialogs.length - 1];
    var root = dialog.querySelector(':scope > [data-toast-root]');
    if (!root) {
      root = document.createElement('div');
      root.className = TOAST_CLASSES;
      root.setAttribute('data-toast-root', '');
      dialog.appendChild(root);
      dialog.addEventListener('close', function () {
        while (page && root.firstChild) page.appendChild(root.firstChild);
      });
    }
    return root;
  }

  function toast(message, type, ms) {
    var root = toastRoot();
    if (!root) return;
    var alert = document.createElement('div');
    alert.className = 'alert text-sm ' + (type === 'success' ? 'alert-success' : 'alert-error');
    alert.setAttribute('role', type === 'success' ? 'status' : 'alert');
    alert.dataset.autoDismiss = String(ms || 3000);
    var span = document.createElement('span');
    span.textContent = message;
    alert.appendChild(span);
    root.appendChild(alert);
    scheduleDismiss(root);
  }

  // Partials render their own .toast wrapper; its alerts join the shared container.
  function adoptToasts() {
    document.querySelectorAll('.toast[data-auto-dismiss]').forEach(function (wrapper) {
      var root = toastRoot();
      if (!root) return;
      Array.prototype.slice.call(wrapper.children).forEach(function (child) {
        if (!child.dataset.autoDismiss) child.dataset.autoDismiss = wrapper.dataset.autoDismiss;
        root.appendChild(child);
      });
      wrapper.parentNode.removeChild(wrapper);
    });
  }

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

  onReady(function () { adoptToasts(); scheduleDismiss(document); });

  document.addEventListener('htmx:afterSwap', function (e) {
    adoptToasts();
    scheduleDismiss(document);
    // An error toast is a swap too, but the edit it refused is still open.
    if (e && e.detail && e.detail.failed) return;
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

  LR.toast = toast;
  LR.toastRoot = toastRoot;
  LR.scheduleDismiss = scheduleDismiss;
  LR.clearRings = clearRings;
})();
