// Global keyboard shortcuts for the search command palette
document.addEventListener('keydown', (e) => {
  // Cmd/Ctrl+K opens the palette from anywhere, even while typing
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault();
    window.dispatchEvent(new CustomEvent('open-search-palette'));
    return;
  }

  // Ignore "/" while typing in an input, textarea, or contenteditable
  const activeEl = document.activeElement;
  const isTyping = activeEl.tagName === 'INPUT' ||
                   activeEl.tagName === 'TEXTAREA' ||
                   activeEl.isContentEditable;

  if (isTyping) return;

  if (e.key === '/') {
    e.preventDefault();
    window.dispatchEvent(new CustomEvent('open-search-palette'));
  }
});
