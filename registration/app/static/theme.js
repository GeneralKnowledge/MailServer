// Theme choice: "auto" (follow the device), "light" or "dark".
// Loaded in <head> so the page never flashes the wrong theme.
(function () {
  var KEY = "mail-theme", order = ["auto", "dark", "light"];
  var labels = { auto: "Theme: auto", dark: "Theme: dark", light: "Theme: light" };
  function get() { try { return localStorage.getItem(KEY) || "auto"; } catch (e) { return "auto"; } }
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
      try { localStorage.setItem(KEY, next); } catch (e) {}
      apply(next);
      btn.textContent = labels[next];
    });
  });
})();
