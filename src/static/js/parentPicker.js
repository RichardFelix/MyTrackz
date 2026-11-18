// Create Custom: picking a parent show or season from the search results.
// Each result button carries data-parent-pick="<prefix>" plus the label and
// id to copy, so titles never pass through inline JavaScript (a quote in a
// title used to break the old onclick handler). Delegated, because the
// results arrive as HTMX fragments.
document.addEventListener('click', (e) => {
  const button = e.target.closest('[data-parent-pick]');
  if (!button) return;

  const prefix = button.dataset.parentPick;
  document.getElementById(`${prefix}-input`).value = button.dataset.label;
  document.getElementById(`${prefix}-id`).value = button.dataset.id;
  document.getElementById(`${prefix}-results`).replaceChildren();
});
