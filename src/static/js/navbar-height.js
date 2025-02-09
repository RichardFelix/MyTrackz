document.addEventListener("DOMContentLoaded", function () {
  const navbar = document.querySelector(".navbar");

  const updateNavHeight = () => {
    const height = navbar.getBoundingClientRect().height;
    document.documentElement.style.setProperty(
      "--navbar-height",
      `${height}px`
    );
  };

  updateNavHeight();
  window.addEventListener("resize", updateNavHeight);
});
