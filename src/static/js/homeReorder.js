// Home screen Custom order editor (/?reorder=1).
//
// Each [data-reorder-row] holds its titles as [data-reorder-key] children.
// A title moves by dragging its grip (pointer events, so mouse, pen and touch
// all work; the grip is touch-action: none, so the page still scrolls when
// you touch anywhere else) or with its arrow buttons. Every change re-posts
// that row's whole order, debounced, because the server replaces the row.
//
// While dragging, a copy of the tile (the "ghost") follows the pointer and
// the real tile stays in the row as a dashed placeholder. Its neighbours
// slide into their new places (FLIP: measure, move in the DOM, then animate
// from the old position). Styling is inline so no Tailwind rebuild is needed.
(function () {
  const SAVE_DELAY_MS = 400;
  const EDGE_PX = 70;
  const SCROLL_STEP_PX = 12;
  const SLIDE = "transform 200ms cubic-bezier(0.2, 0.8, 0.3, 1)";
  let drag = null;

  function items(row) {
    return Array.from(row.children).filter((el) => el.matches("[data-reorder-key]"));
  }

  function setStatus(text) {
    const status = document.querySelector("[data-reorder-status]");
    if (status) status.textContent = text;
  }

  function refresh(row) {
    const list = items(row);
    list.forEach((item, index) => {
      const position = item.querySelector("[data-reorder-position]");
      if (position) position.textContent = String(index + 1);
      const earlier = item.querySelector('[data-reorder-move="-1"]');
      const later = item.querySelector('[data-reorder-move="1"]');
      if (earlier) earlier.disabled = index === 0;
      if (later) later.disabled = index === list.length - 1;
    });
  }

  function formBody(row) {
    const body = new FormData();
    body.append("status", row.dataset.reorderStatusKey);
    body.append("media_type", row.dataset.reorderMediaType);
    // one JSON field, not one field per title: Django refuses more than
    // 1000 form fields per request
    body.append("order", JSON.stringify(items(row).map((item) => item.dataset.reorderKey)));
    return body;
  }

  // Saving. A change waits SAVE_DELAY_MS (a burst of moves is one save), then
  // joins a queue that sends one request at a time, each reading the row's
  // order when it goes out, so an older order can never land after a newer
  // one. A row that failed stays failed until a later save of it succeeds.
  const timers = new Map();
  const queued = new Set();
  const failed = new Set();
  let inFlight = false;
  let idleWaiters = [];

  function busy() {
    return inFlight || timers.size > 0 || queued.size > 0;
  }

  function settle() {
    if (busy()) return;
    setStatus(failed.size ? "Couldn't save. Reload and try again." : "Saved");
    idleWaiters.forEach((resolve) => resolve());
    idleWaiters = [];
  }

  function pump() {
    if (inFlight) return;
    const row = queued.values().next().value;
    if (!row) {
      settle();
      return;
    }
    queued.delete(row);
    inFlight = true;
    setStatus("Saving…");
    fetch(row.dataset.reorderUrl, {
      method: "POST",
      body: formBody(row),
      credentials: "same-origin",
      headers: { "X-CSRFToken": row.dataset.csrfToken },
    })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        failed.delete(row);
      })
      .catch(() => failed.add(row))
      .finally(() => {
        inFlight = false;
        pump();
      });
  }

  function scheduleSave(row) {
    refresh(row);
    setStatus("Saving…");
    window.clearTimeout(timers.get(row));
    timers.set(
      row,
      window.setTimeout(() => {
        timers.delete(row);
        queued.add(row);
        pump();
      }, SAVE_DELAY_MS),
    );
  }

  // Send everything still waiting now; resolves once the queue is empty.
  function flush() {
    timers.forEach((timer, row) => {
      window.clearTimeout(timer);
      queued.add(row);
    });
    timers.clear();
    const idle = new Promise((resolve) => idleWaiters.push(resolve));
    pump();
    return idle;
  }

  // Run a DOM move, then slide every tile in the row from where it was
  // (including any slide already under way) to where it now sits.
  function animateMove(row, mutate) {
    const list = items(row);
    const before = new Map(list.map((el) => [el, el.getBoundingClientRect()]));
    mutate();
    // while dragging, "none" also holds off the hover lift on tiles passed over
    const rest = drag ? "none" : "";
    list.forEach((el) => {
      el.style.transition = "none";
      el.style.transform = rest;
    });
    const moved = list.filter((el) => {
      const was = before.get(el);
      const now = el.getBoundingClientRect();
      const dx = was.left - now.left;
      const dy = was.top - now.top;
      if (!dx && !dy) return false;
      el.style.transform = `translate(${dx}px, ${dy}px)`;
      return true;
    });
    if (!moved.length) return;
    row.getBoundingClientRect(); // flush the start positions before animating
    moved.forEach((el) => {
      el.style.transition = SLIDE;
      el.style.transform = rest;
      window.clearTimeout(el._reorderSlideEnd);
      el._reorderSlideEnd = window.setTimeout(() => {
        el.style.transition = "";
      }, 220);
    });
  }

  function moveBy(item, step) {
    const row = item.parentElement;
    const list = items(row);
    const target = list[list.indexOf(item) + step];
    if (!target) return;
    animateMove(row, () => {
      if (step < 0) target.before(item);
      else target.after(item);
    });
    scheduleSave(row);
  }

  // A tile's box in the layout, ignoring the transforms of a slide in
  // progress, so the hit test does not flicker while neighbours animate.
  function layoutBox(el) {
    const parent = el.offsetParent || document.body;
    const parentRect = parent.getBoundingClientRect();
    // the page's own scroll is already in <body>'s rect; an inner scroller's isn't
    const page = parent === document.body || parent === document.documentElement;
    const left =
      parentRect.left + parent.clientLeft - (page ? 0 : parent.scrollLeft) + el.offsetLeft;
    const top =
      parentRect.top + parent.clientTop - (page ? 0 : parent.scrollTop) + el.offsetTop;
    return { left, top, right: left + el.offsetWidth, bottom: top + el.offsetHeight };
  }

  // Move the placeholder to whichever tile the pointer is over.
  function track() {
    const { x, y } = drag;
    const list = items(drag.row);
    const under = list.find((el) => {
      if (el === drag.item) return false;
      const box = layoutBox(el);
      return x >= box.left && x < box.right && y >= box.top && y < box.bottom;
    });
    if (!under) return;
    const placeBefore = list.indexOf(under) < list.indexOf(drag.item);
    animateMove(drag.row, () => {
      if (placeBefore) under.before(drag.item);
      else under.after(drag.item);
    });
    drag.moved = true;
    refresh(drag.row);
  }

  function placeGhost() {
    drag.ghost.style.transform =
      `translate(${drag.x - drag.grabX}px, ${drag.y - drag.grabY}px) scale(1.04) rotate(1.5deg)`;
  }

  // The ghost is a plain copy: no Alpine, no ids, no editing cover.
  function makeGhost(item, rect) {
    const ghost = item.cloneNode(true);
    ghost.setAttribute("x-ignore", "");
    ghost.removeAttribute("id");
    ghost.removeAttribute("data-reorder-key");
    ghost.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
    ghost.querySelectorAll("template, [data-reorder-overlay]").forEach((el) => el.remove());
    ghost.setAttribute("aria-hidden", "true");
    ghost.setAttribute("data-reorder-ghost", "");
    Object.assign(ghost.style, {
      position: "fixed",
      left: "0",
      top: "0",
      width: `${rect.width}px`,
      height: `${rect.height}px`,
      margin: "0",
      zIndex: "70",
      pointerEvents: "none",
      transition: "none",
      transformOrigin: "center",
      boxShadow: "0 28px 50px -12px rgba(0, 0, 0, 0.9), 0 0 0 2px rgb(99, 102, 241)",
      borderRadius: getComputedStyle(item).borderRadius,
      willChange: "transform",
    });
    document.body.appendChild(ghost);
    return ghost;
  }

  // Scroll the page while the pointer sits near the top or bottom edge.
  function autoScroll() {
    if (!drag) return;
    let step = 0;
    if (drag.y < EDGE_PX) step = -SCROLL_STEP_PX;
    else if (drag.y > window.innerHeight - EDGE_PX) step = SCROLL_STEP_PX;
    if (step) {
      window.scrollBy(0, step);
      track();
    }
    drag.frame = window.requestAnimationFrame(autoScroll);
  }

  function clearDraggedStyles(item) {
    Object.assign(item.style, { opacity: "", outline: "", outlineOffset: "" });
  }

  function endDrag() {
    if (!drag) return;
    const { item, row, ghost, moved } = drag;
    window.cancelAnimationFrame(drag.frame);
    drag = null;
    document.documentElement.style.cursor = "";
    items(row).forEach((el) => {
      if (!el.style.transition) el.style.transform = "";
    });

    // settle the ghost into the placeholder's spot, then show the real tile
    const target = item.getBoundingClientRect();
    ghost.style.transition = "transform 180ms ease-out, box-shadow 180ms ease-out";
    ghost.style.transform = `translate(${target.left}px, ${target.top}px)`;
    ghost.style.boxShadow = "0 0 0 0 rgba(0, 0, 0, 0)";
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      ghost.remove();
      clearDraggedStyles(item);
      items(row).forEach((el) => {
        if (el.style.transform === "none") el.style.transform = "";
      });
    };
    ghost.addEventListener("transitionend", finish, { once: true });
    window.setTimeout(finish, 250);

    if (moved) scheduleSave(row);
  }

  document.addEventListener("pointerdown", (event) => {
    const grip = event.target.closest("[data-reorder-grip]");
    if (drag || !grip || !event.isPrimary || event.button !== 0) return;
    const item = grip.closest("[data-reorder-key]");
    if (!item) return;
    event.preventDefault();
    grip.setPointerCapture?.(event.pointerId);

    const rect = item.getBoundingClientRect();
    drag = {
      item,
      row: item.parentElement,
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      grabX: event.clientX - rect.left,
      grabY: event.clientY - rect.top,
      moved: false,
      frame: 0,
      ghost: makeGhost(item, rect),
    };
    placeGhost();
    items(drag.row).forEach((el) => {
      el.style.transform = "none";
    });
    Object.assign(item.style, {
      opacity: "0.25",
      outline: "2px dashed rgb(129, 140, 248)",
      outlineOffset: "-2px",
    });
    document.documentElement.style.cursor = "grabbing";
    drag.frame = window.requestAnimationFrame(autoScroll);
  });

  document.addEventListener(
    "pointermove",
    (event) => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      event.preventDefault();
      drag.x = event.clientX;
      drag.y = event.clientY;
      placeGhost();
      track();
    },
    { passive: false },
  );

  document.addEventListener("pointerup", (event) => {
    if (drag && event.pointerId === drag.pointerId) endDrag();
  });
  document.addEventListener("pointercancel", (event) => {
    if (drag && event.pointerId === drag.pointerId) endDrag();
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-reorder-move]");
    if (!button) return;
    event.preventDefault();
    const item = button.closest("[data-reorder-key]");
    if (item) moveBy(item, Number(button.dataset.reorderMove));
  });

  // Done waits for the saves, so the home page it opens shows the new order.
  // A failed save keeps you here with the error; a second tap leaves anyway.
  document.addEventListener("click", (event) => {
    const done = event.target.closest("[data-reorder-done]");
    if (!done || !busy()) return;
    event.preventDefault();
    flush().then(() => {
      if (!failed.size) window.location.assign(done.href);
    });
  });

  // Leaving some other way (back button, closing the tab): whatever has not
  // been sent yet goes out as a beacon. A request already in flight finishes.
  window.addEventListener("pagehide", () => {
    timers.forEach((timer) => window.clearTimeout(timer));
    const rows = new Set([...timers.keys(), ...queued]);
    timers.clear();
    queued.clear();
    rows.forEach((row) => {
      const body = formBody(row);
      body.append("csrfmiddlewaretoken", row.dataset.csrfToken);
      navigator.sendBeacon(row.dataset.reorderUrl, body);
    });
  });

  function init() {
    document.querySelectorAll("[data-reorder-row]").forEach(refresh);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
