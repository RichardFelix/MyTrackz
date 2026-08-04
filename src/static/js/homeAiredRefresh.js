document.addEventListener("DOMContentLoaded", () => {
  let refreshAt = Number.NaN;
  let refreshTimer;
  let refreshing = false;

  const retryRefresh = () => {
    refreshing = false;
    refreshTimer = window.setTimeout(refreshWhenVisible, 60 * 1000);
  };

  const refreshWhenVisible = () => {
    const section = document.querySelector("[data-home-air-refresh-url]");
    if (
      !section
      || refreshing
      || document.visibilityState !== "visible"
      || Date.now() < refreshAt
    ) {
      return;
    }

    refreshing = true;
    const refreshUrl = section.dataset.homeAirRefreshUrl;
    const handleRequestComplete = (event) => {
      const request = event.detail.requestConfig;
      if (request?.path !== refreshUrl || request?.verb !== "get") return;

      document.removeEventListener("htmx:afterRequest", handleRequestComplete);
      if (event.detail.successful) {
        refreshing = false;
      } else {
        retryRefresh();
      }
    };
    document.addEventListener("htmx:afterRequest", handleRequestComplete);

    window.htmx.ajax("GET", refreshUrl, {
      source: section,
      target: "#in-progress",
      swap: "outerHTML",
    });
  };

  const scheduleRefresh = () => {
    window.clearTimeout(refreshTimer);
    const section = document.querySelector("[data-next-home-air-datetime]");
    if (!section) {
      refreshAt = Number.NaN;
      return;
    }

    refreshAt = Date.parse(section.dataset.nextHomeAirDatetime);
    if (Number.isNaN(refreshAt)) {
      refreshAt = Number.NaN;
      return;
    }

    const delay = refreshAt - Date.now() + 1000;
    if (delay <= 0) {
      refreshWhenVisible();
      return;
    }

    refreshTimer = window.setTimeout(
      scheduleRefresh,
      Math.min(delay, 24 * 60 * 60 * 1000),
    );
  };

  document.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail.target?.id === "in-progress") scheduleRefresh();
  });
  document.addEventListener("visibilitychange", () => {
    if (Date.now() >= refreshAt) refreshWhenVisible();
  });
  scheduleRefresh();
});
