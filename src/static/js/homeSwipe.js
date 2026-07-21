function homeSwipe({ enabled }) {
  return {
    enabled,
    episodeInfoOpen: false,
    dragging: false,
    horizontal: false,
    offset: 0,
    startX: 0,
    startY: 0,
    pointerId: null,
    suppressClick: false,

    start(event) {
      if (!this.enabled || !event.isPrimary || event.button !== 0) return;
      if (event.target.closest("a, button")) return;
      this.dragging = true;
      this.horizontal = false;
      this.startX = event.clientX;
      this.startY = event.clientY;
      this.pointerId = event.pointerId;
      event.currentTarget.setPointerCapture?.(event.pointerId);
    },

    move(event) {
      if (!this.dragging || event.pointerId !== this.pointerId) return;
      const deltaX = event.clientX - this.startX;
      const deltaY = event.clientY - this.startY;

      if (!this.horizontal && Math.abs(deltaY) > Math.abs(deltaX) && Math.abs(deltaY) > 10) {
        this.cancel();
        return;
      }
      if (Math.abs(deltaX) > 10) this.horizontal = true;
      if (!this.horizontal) return;

      event.preventDefault();
      this.offset = Math.max(-104, Math.min(104, deltaX));
    },

    finish(event) {
      if (!this.dragging || event.pointerId !== this.pointerId) return;
      const completedSwipe = this.horizontal && Math.abs(this.offset) >= 72;
      this.suppressClick = this.horizontal;
      this.dragging = false;
      this.horizontal = false;
      this.offset = 0;
      this.pointerId = null;

      if (completedSwipe) this.$refs.swipeAction?.click();
      window.setTimeout(() => { this.suppressClick = false; }, 250);
    },

    cancel() {
      this.dragging = false;
      this.horizontal = false;
      this.offset = 0;
      this.pointerId = null;
    },

    guardClick(event) {
      if (this.suppressClick && event.target !== this.$refs.swipeAction) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
  };
}
