// In-place library edits: the track modal's Update/Delete swap one card/row
// (#library-item-<item id>) instead of reloading the page, so the scroll
// position and the already-loaded infinite-scroll pages are kept.

const SCROLL_ATTRS = ['hx-get', 'hx-trigger', 'hx-swap', 'hx-include', 'hx-indicator'];

// Send the library's live filters so the server can tell whether the edited
// item still belongs in the list. They go as one field because the filter
// names (status, format, launcher) collide with the tracking form's fields.
document.addEventListener('htmx:configRequest', (e) => {
  if (!e.detail.elt.hasAttribute('data-library-save')) return;
  const filters = document.getElementById('filter-form');
  if (filters) {
    e.detail.parameters.library_filters = new URLSearchParams(new FormData(filters)).toString();
  }
});

// The sentinel that will load the next page: htmx marks a "revealed"
// trigger with data-hx-revealed once it has fired.
function activeSentinel(container) {
  return container.querySelector(
    ':scope > [data-library-item][hx-trigger^="revealed"]:not([data-hx-revealed])',
  );
}

// Each removed card shifts every later page back by one; the server pulls
// the next page back by this count so no item is skipped.
function bumpRemoved(sentinel) {
  const url = new URL(sentinel.getAttribute('hx-get'), window.location.origin);
  url.searchParams.set('removed', Number(url.searchParams.get('removed') || 0) + 1);
  sentinel.setAttribute('hx-get', url.pathname + url.search);
  htmx.process(sentinel);
}

document.addEventListener('htmx:beforeSwap', (e) => {
  const target = e.detail.target;
  if (!target || !target.hasAttribute('data-library-item')) return;

  // Table rows keep their Alpine state across an innerHTML swap
  if (target.hasAttribute('x-data') && window.Alpine) {
    Alpine.$data(target).trackOpen = false;
  }

  if (e.detail.xhr.getResponseHeader('HX-Reswap') !== 'delete') return;

  let sentinel = activeSentinel(target.parentElement);
  if (sentinel === target) {
    // Hand the next-page trigger to the previous card before this one goes
    const prev = target.previousElementSibling;
    if (!prev) {
      window.location.reload();
      return;
    }
    SCROLL_ATTRS.forEach((name) => {
      if (target.hasAttribute(name)) prev.setAttribute(name, target.getAttribute(name));
    });
    prev.removeAttribute('data-hx-revealed');
    sentinel = prev;
  }
  if (sentinel) bumpRemoved(sentinel);
});
