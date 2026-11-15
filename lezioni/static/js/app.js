/* Lezioni — small progressive enhancements. Every page works without JS. */
(function () {
  "use strict";
  var I18N = {};
  try { I18N = JSON.parse(document.getElementById("i18n").textContent); } catch (e) { /* ignore */ }
  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  function toast(msg) {
    var t = $("#toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(t._timer);
    t._timer = setTimeout(function () { t.classList.remove("show"); }, 2600);
  }

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "k" + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
  }

  /* navigation drawer */
  $$("[data-nav-toggle]").forEach(function (b) {
    b.addEventListener("click", function () { document.body.classList.toggle("nav-open"); });
  });
  $$("[data-nav-close]").forEach(function (b) {
    b.addEventListener("click", function () { document.body.classList.remove("nav-open"); });
  });

  /* flashes */
  $$("[data-dismiss]").forEach(function (b) {
    b.addEventListener("click", function () { b.closest(".flash").remove(); });
  });

  /* theme */
  $$("[data-theme-toggle]").forEach(function (b) {
    b.addEventListener("click", function () {
      var dark = document.documentElement.getAttribute("data-theme") !== "dark";
      if (dark) document.documentElement.setAttribute("data-theme", "dark");
      else document.documentElement.removeAttribute("data-theme");
      try { localStorage.setItem("lezioni-theme", dark ? "dark" : "light"); } catch (e) { /* ignore */ }
    });
  });

  /* one-time keys against double submissions (payments) */
  $$("[data-idempotency]").forEach(function (i) { if (!i.value) i.value = uuid(); });

  /* clickable rows */
  $$("[data-href]").forEach(function (row) {
    row.addEventListener("click", function (e) {
      if (e.target.closest("a, button, input, select, textarea, label, form")) return;
      window.location = row.getAttribute("data-href");
    });
  });

  /* confirmation dialog (+ optional reason) */
  var dialog = $("#confirm-dialog");
  function askConfirm(form) {
    if (!dialog || typeof dialog.showModal !== "function") {
      if (form.dataset.reason) {
        var r = window.prompt(form.dataset.confirm || "", "");
        if (r === null || !r.trim()) return false;
        form.querySelector('input[name="reason"]').value = r.trim();
        return true;
      }
      return window.confirm(form.dataset.confirm || "");
    }
    $("[data-confirm-text]", dialog).textContent = form.dataset.confirm || "";
    var wrap = $("[data-confirm-reason-wrap]", dialog);
    var reasonInput = $("#confirm-reason", dialog);
    wrap.classList.toggle("hidden", !form.dataset.reason);
    reasonInput.value = "";
    dialog.returnValue = "";
    dialog.showModal();
    if (form.dataset.reason) reasonInput.focus();
    dialog.onclose = function () {
      if (dialog.returnValue !== "ok") return;
      if (form.dataset.reason) {
        var v = reasonInput.value.trim();
        if (!v) { toast(I18N.reason_required || "!"); return; }
        form.querySelector('input[name="reason"]').value = v;
      }
      form.dataset.confirmed = "1";
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    };
    return false;
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.dataset.confirm && form.dataset.confirmed !== "1") {
      e.preventDefault();
      askConfirm(form);
      return;
    }
    // block double clicks
    if (form.dataset.submitting === "1") { e.preventDefault(); return; }
    form.dataset.submitting = "1";
    var btn = e.submitter || form.querySelector('[type="submit"]');
    if (btn) btn.classList.add("is-loading");
    setTimeout(function () { form.dataset.submitting = ""; if (btn) btn.classList.remove("is-loading"); }, 8000);
  }, true);

  /* select all checkboxes */
  $$("[data-check-all]").forEach(function (box) {
    box.addEventListener("change", function () {
      $$('input[type="checkbox"][name="' + box.dataset.checkAll + '"]').forEach(function (c) { c.checked = box.checked; });
    });
  });

  /* attendance: "everyone present" */
  $$("[data-all-status]").forEach(function (b) {
    b.addEventListener("click", function () {
      var status = b.dataset.allStatus;
      $$('input[type="radio"][value="' + status + '"][data-att]').forEach(function (r) { r.checked = true; });
      toast(b.dataset.toast || "");
    });
  });

  /* stars */
  $$(".stars").forEach(function (group) {
    var inputs = $$('input[type="radio"]', group);
    function paint() {
      var val = 0;
      inputs.forEach(function (i) { if (i.checked) val = parseInt(i.value, 10) || 0; });
      $$("label", group).forEach(function (l) {
        var n = parseInt(l.dataset.n, 10);
        l.classList.toggle("on", n > 0 && n <= val);
      });
    }
    inputs.forEach(function (i) {
      i.addEventListener("change", paint);
      i.addEventListener("click", function () {
        if (i.dataset.wasChecked === "1") { i.checked = false; var none = group.querySelector('input[value=""]'); if (none) none.checked = true; }
        inputs.forEach(function (o) { o.dataset.wasChecked = o.checked ? "1" : ""; });
        paint();
      });
    });
    inputs.forEach(function (o) { o.dataset.wasChecked = o.checked ? "1" : ""; });
    paint();
  });

  /* show/hide blocks driven by a radio or select */
  $$("[data-toggle-group]").forEach(function (ctrl) {
    var name = ctrl.dataset.toggleGroup;
    function apply() {
      var value = ctrl.type === "radio" ? ($('input[name="' + ctrl.name + '"]:checked') || {}).value : ctrl.value;
      $$('[data-show-for="' + name + '"]').forEach(function (el) {
        var allowed = el.dataset.when.split(",");
        el.classList.toggle("hidden", allowed.indexOf(value) === -1);
        $$("input, select, textarea", el).forEach(function (f) { f.disabled = allowed.indexOf(value) === -1; });
      });
    }
    ctrl.addEventListener("change", apply);
    if (ctrl.type !== "radio" || ctrl.checked) apply();
  });

  /* collect payment: live total */
  var collect = $("[data-collect]");
  if (collect) {
    var cents = function (v) {
      v = (v || "").replace(/[€\s]/g, "");
      if (v.indexOf(",") > -1 && v.indexOf(".") > -1) {
        v = v.lastIndexOf(",") > v.lastIndexOf(".") ? v.replace(/\./g, "").replace(",", ".") : v.replace(/,/g, "");
      } else { v = v.replace(",", "."); }
      var n = parseFloat(v);
      return isNaN(n) ? null : Math.round(n * 100);
    };
    var fmt = function (c) {
      var s = (c / 100).toFixed(2).split(".");
      return (collect.dataset.symbol || "€") + " " + s[0].replace(/\B(?=(\d{3})+(?!\d))/g, ".") + "," + s[1];
    };
    var update = function () {
      var mode = ($('input[name="mode"]:checked', collect) || {}).value;
      var total = 0;
      if (mode === "lessons") {
        var n = parseInt(($('[name="lessons"]', collect) || {}).value, 10) || 0;
        total = n * (cents(($('[name="unit_price"]', collect) || {}).value) || 0);
      } else if (mode === "package") {
        var opt = $('[name="package_id"]', collect);
        var sel = opt && opt.options[opt.selectedIndex];
        total = sel ? parseInt(sel.dataset.price || "0", 10) : 0;
      }
      var out = $("[data-collect-total]", collect);
      if (out) out.textContent = fmt(total);
      var paid = $('[name="amount_paid"]', collect);
      if (paid && !paid.dataset.touched && mode !== "payment_only") paid.value = total ? (total / 100).toFixed(2).replace(".", ",") : "";
    };
    $$("input, select", collect).forEach(function (el) { el.addEventListener("input", update); el.addEventListener("change", update); });
    var paidInput = $('[name="amount_paid"]', collect);
    if (paidInput) paidInput.addEventListener("input", function () { paidInput.dataset.touched = "1"; });
    update();
  }

  /* notebook autosave */
  var nb = $("[data-autosave]");
  if (nb) {
    var state = $("[data-save-state]");
    var version = parseInt(nb.dataset.version, 10);
    var timer = null, inflight = false, dirty = false;
    var setState = function (cls, text) { if (state) { state.className = "save-state " + cls; state.textContent = text; } };
    var payload = function () {
      var data = { version: version };
      $$("[data-field]", nb).forEach(function (f) { data[f.dataset.field] = f.value; });
      return data;
    };
    var save = function () {
      if (inflight) { dirty = true; return; }
      inflight = true; dirty = false;
      setState("saving", I18N.saving);
      fetch(nb.dataset.autosave, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
        body: JSON.stringify(payload()),
        credentials: "same-origin"
      }).then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
        .then(function (res) {
          inflight = false;
          if (res.body.ok) {
            version = res.body.version;
            nb.dataset.version = version;
            var vi = $('input[name="version"]', nb); if (vi) vi.value = version;
            setState("saved", I18N.saved);
            if (dirty) save();
          } else if (res.status === 409) {
            setState("error", I18N.conflict);
          } else {
            setState("error", I18N.save_error);
          }
        })
        .catch(function () { inflight = false; setState("error", I18N.offline); });
    };
    $$("[data-field]", nb).forEach(function (f) {
      f.addEventListener("input", function () {
        clearTimeout(timer);
        setState("", "…");
        timer = setTimeout(save, 1200);
      });
    });
    window.addEventListener("beforeunload", function (e) {
      if (timer && state && state.textContent === "…") { save(); }
    });
  }

})();
