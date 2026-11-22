/* Shared error toasts for htmx responses.
   Server errors built by error_response() arrive retargeted at #toast-root.
   This script (a) moves them inside an open <dialog>, which would otherwise
   draw over the page-level toast, (b) turns non-HTML error bodies (JSON
   detail, plain text, empty) and every 5xx body the app did not build into
   a toast instead of a page swap, (c) drops
   errors from requests the user did not start (polling, load, intersect,
   revealed), and (d) closes a dialog whose content failed to load.
   Field-error JSON ({"errors": ...}) is left to the page that owns the form. */
(function () {
  var ROOT_ID = 'toast-root';
  var TOAST_CLASSES = 'toast toast-bottom toast-end z-50';
  var DISMISS_MS = '6000';
  var PASSIVE_EVENT = /^(intersect|revealed)$/;
  var PASSIVE_ATTR = /\b(every|load)\b/;

  function toastRoot() {
    var dialogs = document.querySelectorAll('dialog[open]');
    if (!dialogs.length) return document.getElementById(ROOT_ID);
    var dialog = dialogs[dialogs.length - 1];
    var root = dialog.querySelector(':scope > [data-toast-root]');
    if (!root) {
      root = document.createElement('div');
      root.className = TOAST_CLASSES;
      root.setAttribute('data-toast-root', '');
      dialog.appendChild(root);
    }
    return root;
  }

  function parseJson(text) {
    try { return JSON.parse(text); } catch (e) { return null; }
  }

  function showToast(message) {
    var root = toastRoot();
    if (!root) return;
    var alert = document.createElement('div');
    alert.className = 'alert alert-error text-sm';
    alert.setAttribute('role', 'alert');
    alert.dataset.autoDismiss = DISMISS_MS;
    var span = document.createElement('span');
    span.textContent = message;
    alert.appendChild(span);
    root.appendChild(alert);
    if (window.LR && LR.scheduleDismiss) LR.scheduleDismiss(root);
  }

  // Polling and load fire with no event, like htmx.ajax(); the source's own
  // hx-trigger tells them apart.
  function isBackground(config, source) {
    var trigger = config.triggeringEvent;
    if (trigger) return PASSIVE_EVENT.test(trigger.type);
    return !!source && PASSIVE_ATTR.test(source.getAttribute('hx-trigger') || '');
  }

  // Openers call showModal() before the content arrives; a failed load would
  // leave the dialog open on nothing. Requests from inside the dialog are
  // actions on content that did load, so the dialog stays.
  function closeUnloadedDialog(config, source) {
    if (String(config.verb).toLowerCase() !== 'get' || !config.target) return;
    var dialog = config.target.closest('dialog[open]');
    if (dialog && !(source && dialog.contains(source))) dialog.close();
  }

  // beforeSwap fires on the target, so the source element is noted here.
  document.addEventListener('htmx:beforeRequest', function (evt) {
    if (evt.detail.xhr) evt.detail.xhr.lrSource = evt.detail.elt;
  });

  document.addEventListener('htmx:beforeSwap', function (evt) {
    var detail = evt.detail;
    var xhr = detail.xhr;
    if (!xhr || xhr.status < 400) return;

    var config = detail.requestConfig || {};
    if (isBackground(config, xhr.lrSource)) {
      detail.shouldSwap = false;
      return;
    }
    closeUnloadedDialog(config, xhr.lrSource);

    if (xhr.getResponseHeader('HX-Retarget')) {
      if (detail.target && detail.target.id === ROOT_ID) detail.target = toastRoot();
      detail.shouldSwap = true;
      return;
    }

    // 4xx HTML goes to its normal target; 5xx HTML is a proxy or crash page.
    var contentType = (xhr.getResponseHeader('Content-Type') || '').toLowerCase();
    var isHtml = contentType.indexOf('text/html') === 0 && xhr.responseText;
    if (isHtml && xhr.status < 500) return;

    detail.shouldSwap = false;
    var body = contentType.indexOf('json') !== -1 ? parseJson(xhr.responseText) : null;
    if (body && body.errors) return;
    var message = body && (body.detail || body.error);
    showToast(typeof message === 'string' ? message : 'Request failed (' + xhr.status + ')');
  });
})();
