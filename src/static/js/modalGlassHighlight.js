// Liquid-glass modals: drive the specular highlight (.modal-glass::after)
// from the pointer position. Delegated so HTMX-injected modals just work.
(() => {
  if (!window.matchMedia('(hover: hover)').matches) return;
  document.addEventListener(
    'pointermove',
    (event) => {
      const panel = event.target.closest?.('.modal-glass');
      if (!panel) return;
      const rect = panel.getBoundingClientRect();
      panel.style.setProperty('--mx', `${event.clientX - rect.left}px`);
      panel.style.setProperty('--my', `${event.clientY - rect.top + panel.scrollTop}px`);
    },
    { passive: true },
  );
})();
