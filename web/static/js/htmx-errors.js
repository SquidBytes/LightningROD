/* Shared error toasts for htmx responses.
   Server errors built by error_response() arrive retargeted at #toast-root.
   This script (a) moves them inside an open <dialog>, which would otherwise
   draw over the page-level toast, and (b) turns non-HTML error bodies
   (JSON detail, plain text, empty) into a toast instead of a page swap.
   Field-error JSON ({"errors": ...}) is left to the page that owns the form. */
(function () {
  var ROOT_ID = 'toast-root';
  var TOAST_CLASSES = 'toast toast-bottom toast-end z-50';
  var DISMISS_MS = '6000';

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

  document.addEventListener('htmx:beforeSwap', function (evt) {
    var detail = evt.detail;
    var xhr = detail.xhr;
    if (!xhr || xhr.status < 400) return;

    if (xhr.getResponseHeader('HX-Retarget')) {
      if (detail.target && detail.target.id === ROOT_ID) detail.target = toastRoot();
      return;
    }

    var contentType = (xhr.getResponseHeader('Content-Type') || '').toLowerCase();
    if (contentType.indexOf('text/html') === 0 && xhr.responseText) return;

    detail.shouldSwap = false;
    var body = contentType.indexOf('json') !== -1 ? parseJson(xhr.responseText) : null;
    if (body && body.errors) return;
    var message = body && (body.detail || body.error);
    showToast(typeof message === 'string' ? message : 'Request failed (' + xhr.status + ')');
  });
})();
