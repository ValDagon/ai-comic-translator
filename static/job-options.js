(function () {
  var UI_MODE_KEY = "comicTranslator.uiMode";

  function byId(id) {
    return document.getElementById(id);
  }

  function isMarkedHidden(node) {
    return !!(node && (node.hidden || node.classList.contains("opt-hidden")));
  }

  /** Скрытые блоки убираем из UI; disabled — чтобы не ушли в POST. */
  function setVisible(el, visible) {
    if (!el) return;
    if (visible) {
      el.hidden = false;
      el.removeAttribute("hidden");
      el.classList.remove("opt-hidden");
      el.style.removeProperty("display");
    } else {
      el.hidden = true;
      el.setAttribute("hidden", "hidden");
      el.classList.add("opt-hidden");
      el.style.display = "none";
    }
    el.querySelectorAll("input, select, textarea, button").forEach(function (input) {
      if (input.type === "submit") return;
      if (!visible) {
        input.disabled = true;
        return;
      }
      var host = input.closest(".opt-row, .opt-field, .opt-advanced, .opt-group");
      var blocked = false;
      while (host && el.contains(host)) {
        if (host !== el && isMarkedHidden(host)) {
          blocked = true;
          break;
        }
        host = host.parentElement
          ? host.parentElement.closest(".opt-row, .opt-field, .opt-advanced, .opt-group")
          : null;
      }
      input.disabled = blocked;
    });
  }

  function syncJobOptions() {
    var kind = byId("job-kind")?.value || "full";
    var skipExtract = byId("skip_extract")?.checked;
    var useExisting = byId("use_existing_translations")?.checked;

    var showExtract = kind === "full" || kind === "extract";
    var showTranslate = kind === "full" || kind === "translate";
    var showRender = kind === "full" || kind === "render";

    setVisible(byId("opt-group-extract"), showExtract);
    setVisible(byId("opt-group-translate"), showTranslate);
    setVisible(byId("opt-group-render"), showRender);

    setVisible(byId("opt-row-skip-extract"), kind === "full");

    var showExtractActions =
      kind === "extract" || (kind === "full" && !skipExtract);
    setVisible(byId("opt-advanced-extract"), showExtractActions);
    setVisible(byId("opt-row-prep-manual"), showExtractActions);
    setVisible(byId("opt-field-mit-repo"), showExtractActions);

    if (skipExtract && byId("prep_manual")?.checked) {
      byId("prep_manual").checked = false;
    }

    var showTranslateApi =
      (kind === "full" || kind === "translate") && !useExisting;
    // Язык — общий параметр OpenRouter, не часть «готового перевода».
    setVisible(byId("opt-field-target-lang"), showTranslate);
    setVisible(byId("opt-row-use-existing"), showTranslate);
    setVisible(byId("opt-advanced-translate"), showTranslate);
    setVisible(byId("opt-row-save-translations"), showTranslateApi);
    setVisible(byId("opt-field-model"), showTranslateApi);
    setVisible(byId("opt-field-pages-per-batch"), showTranslateApi);
    setVisible(byId("opt-row-strict"), showTranslate);

    if (useExisting && byId("save_translations")?.checked) {
      byId("save_translations").checked = false;
    }
  }

  function readDefaults() {
    var raw = byId("job-defaults-data");
    if (!raw) return null;
    try {
      return JSON.parse(raw.textContent);
    } catch (_e) {
      return null;
    }
  }

  function resetJobDefaults() {
    var d = readDefaults();
    if (!d) return;

    var kindEl = byId("job-kind");
    if (kindEl && kindEl.tagName === "SELECT") {
      kindEl.value = "full";
    }
    if (byId("skip_extract")) byId("skip_extract").checked = false;
    if (byId("prep_manual")) byId("prep_manual").checked = false;
    if (byId("use_existing_translations")) byId("use_existing_translations").checked = false;
    if (byId("save_translations")) byId("save_translations").checked = true;
    if (byId("strict")) byId("strict").checked = false;

    if (byId("mit_repo")) byId("mit_repo").value = d.mit_repo || "";
    if (byId("model")) byId("model").value = d.model || "";
    if (byId("pages_per_batch")) byId("pages_per_batch").value = d.pages_per_batch ?? "";
    if (byId("font_path")) byId("font_path").value = d.font_path || "";
    if (byId("font_scale")) byId("font_scale").value = d.font_scale ?? "";
    if (byId("target_lang")) {
      byId("target_lang").value = d.target_lang || "ru";
    }

    syncJobOptions();
  }

  function applyUiMode(mode) {
    var isDev = mode === "dev";
    document.documentElement.classList.toggle("ui-dev", isDev);
    var switchEl = byId("ui-mode-switch");
    if (!switchEl) return;
    switchEl.querySelectorAll("button[data-mode]").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-mode") === mode);
    });
  }

  function bootUiMode() {
    var form = byId("job-form");
    if (!form) return;

    var available = form.getAttribute("data-ui-dev-available") === "true";
    if (!available) {
      applyUiMode("user");
      try {
        localStorage.removeItem(UI_MODE_KEY);
      } catch (_e) {}
      return;
    }

    var stored = "user";
    try {
      stored = localStorage.getItem(UI_MODE_KEY) || "user";
    } catch (_e) {}
    if (stored !== "dev" && stored !== "user") stored = "user";
    applyUiMode(stored);

    var switchEl = byId("ui-mode-switch");
    if (!switchEl) return;
    switchEl.addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-mode]");
      if (!btn) return;
      var mode = btn.getAttribute("data-mode");
      applyUiMode(mode);
      try {
        localStorage.setItem(UI_MODE_KEY, mode);
      } catch (_e) {}
    });
  }

  function bootUploadDrop() {
    var drop = byId("upload-drop");
    var input = byId("upload-files");
    var chosen = byId("upload-chosen");
    if (!drop || !input) return;

    function updateChosen() {
      if (!chosen) return;
      var files = input.files;
      if (!files || !files.length) {
        chosen.textContent = "";
        return;
      }
      if (files.length === 1) {
        chosen.textContent = files[0].name;
        return;
      }
      var prefix =
        (window.__i18n && window.__i18n.filesSelected) || "Files selected: ";
      chosen.textContent = prefix + files.length;
    }

    input.addEventListener("change", updateChosen);

    ["dragenter", "dragover"].forEach(function (evt) {
      drop.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        drop.classList.add("is-dragover");
      });
    });

    ["dragleave", "drop"].forEach(function (evt) {
      drop.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        drop.classList.remove("is-dragover");
      });
    });

    drop.addEventListener("drop", function (e) {
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      try {
        input.files = files;
        updateChosen();
      } catch (_err) {
        // Some browsers block assigning FileList; native picker still works.
      }
    });

    var form = byId("upload-form");
    if (form) {
      form.addEventListener("submit", function (e) {
        var files = input.files;
        if (!files || files.length < 50) return;
        var msg =
          (window.__i18n && window.__i18n.uploadManyConfirm) ||
          "You selected many files. Continue?";
        if (!window.confirm(msg)) {
          e.preventDefault();
        }
      });
    }
  }

  function bootJobOptions() {
    var kind = byId("job-kind");
    if (!kind) return;

    if (kind.tagName === "SELECT") {
      kind.addEventListener("change", syncJobOptions);
    }
    ["skip_extract", "prep_manual", "use_existing_translations"].forEach(function (id) {
      var el = byId(id);
      if (el) el.addEventListener("change", syncJobOptions);
    });

    var resetBtn = byId("job-reset-defaults");
    if (resetBtn) {
      resetBtn.addEventListener("click", function (e) {
        e.preventDefault();
        resetJobDefaults();
      });
    }

    syncJobOptions();
  }

  /**
   * Generic confirm() for state-changing forms (delete project/account, …).
   * Uses a data-confirm attribute (normal Jinja auto-escaping) instead of an
   * inline onsubmit="…confirm({{ x | tojson }})…" — tojson doesn't escape
   * `"`, so embedding it inside a double-quoted HTML attribute lets a value
   * containing a quote (e.g. a project name) break out of the attribute.
   */
  function bootConfirmForms() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      var msg = form.getAttribute("data-confirm");
      if (msg == null) return;
      if (!window.confirm(msg)) {
        e.preventDefault();
      }
    });
  }

  function boot() {
    bootUiMode();
    bootJobOptions();
    bootUploadDrop();
    bootConfirmForms();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
