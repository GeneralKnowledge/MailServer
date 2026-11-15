// Live username availability check. The server re-checks on submit.
(function () {
  var form = document.getElementById("register");
  if (!form) return;
  var input = document.getElementById("username");
  var status = document.getElementById("username-status");
  var token = form.querySelector("input[name=token]").value;
  var timer;
  input.addEventListener("input", function () {
    input.value = input.value.toLowerCase();
    clearTimeout(timer);
    status.className = "hint";
    if (input.value.length < 3) { status.textContent = "At least 3 characters."; return; }
    timer = setTimeout(function () {
      var url = form.dataset.check + "?u=" + encodeURIComponent(input.value);
      fetch(url, { credentials: "same-origin", headers: { "X-Invite-Token": token } })
        .then(function (r) { return r.json(); })
        .then(function (d) { status.textContent = d.message; status.className = "hint " + (d.ok ? "ok" : "bad"); })
        .catch(function () { status.textContent = ""; });
    }, 400);
  });
})();
