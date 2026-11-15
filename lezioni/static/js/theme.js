(function () {
  var theme = null;
  try { theme = localStorage.getItem("lezioni-theme"); } catch (e) { /* storage unavailable */ }
  if (!theme && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) theme = "dark";
  if (theme === "dark") document.documentElement.setAttribute("data-theme", "dark");
})();
