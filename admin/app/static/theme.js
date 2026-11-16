// Theme choice: "auto" (follow the device), "light" or "dark".
// Loaded in <head> so the page never flashes the wrong theme.
// The choice is shared with mailcow's login page and SOGo on the same origin:
// mailcow's own toggle stores "mailcow_theme" (dark/light) and wins, as the
// most recent explicit choice; "auto" clears it. SOGo reads the same keys.
(function () {
  var KEY = "mail-theme", MAILCOW_KEY = "mailcow_theme", order = ["auto", "dark", "light"];
  var labels = { auto: "Theme: auto", dark: "Theme: dark", light: "Theme: light" };
  function get() {
    try { return localStorage.getItem(MAILCOW_KEY) || localStorage.getItem(KEY) || "auto"; }
    catch (e) { return "auto"; }
  }
  function save(t) {
    try {
      localStorage.setItem(KEY, t);
      if (t === "auto") localStorage.removeItem(MAILCOW_KEY);
      else localStorage.setItem(MAILCOW_KEY, t);
    } catch (e) {}
  }
  function apply(t) {
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
  }
  apply(get());
  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;
    btn.hidden = false;
    btn.textContent = labels[get()];
    btn.addEventListener("click", function () {
      var next = order[(order.indexOf(get()) + 1) % order.length];
      save(next);
      apply(next);
      btn.textContent = labels[next];
    });
  });
})();
