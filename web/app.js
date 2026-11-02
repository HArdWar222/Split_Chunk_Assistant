/* SplitChunkAssistant P0 前端（零依赖，手写 JS） */
(function () {
  "use strict";

  var state = {
    data: null,      // /api/files 载荷
    config: null,    // /api/config 载荷
    filter: "",
    selected: null   // 当前选中的 file entry
  };

  var el = {
    status: document.getElementById("status"),
    stats: document.getElementById("stats"),
    tree: document.getElementById("tree"),
    detail: document.getElementById("detail"),
    search: document.getElementById("search"),
    phase: document.getElementById("phase"),
    btnRefresh: document.getElementById("btn-refresh"),
    btnSettings: document.getElementById("btn-settings"),
    dialog: document.getElementById("settings-dialog"),
    form: document.getElementById("settings-form"),
    chunksDir: document.getElementById("cfg-chunks-dir"),
    facRoot: document.getElementById("cfg-fac-root"),
    port: document.getElementById("cfg-port"),
    sourceRoots: document.getElementById("cfg-source-roots"),
    cfgErrors: document.getElementById("cfg-errors"),
    btnCancel: document.getElementById("btn-cancel"),
    toast: document.getElementById("toast")
  };

  // ---------- 工具 ----------
  function showToast(msg, kind) {
    el.toast.textContent = msg;
    el.toast.className = "toast" + (kind ? " " + kind : "");
    el.toast.hidden = false;
    window.clearTimeout(showToast._t);
    showToast._t = window.setTimeout(function () { el.toast.hidden = true; }, 2600);
  }

  function setStatus(text, kind) {
    el.status.textContent = text;
    el.status.className = "status" + (kind ? " " + kind : "");
  }

  function fmtInt(n) {
    if (typeof n !== "number" || !isFinite(n)) return "-";
    return n.toLocaleString("zh-CN");
  }

  function getJSON(url) {
    return fetch(url, { headers: { "Accept": "application/json" } }).then(function (resp) {
      return resp.json().then(function (body) {
        if (!resp.ok || body.ok === false) {
          var err = new Error(body.error || ("HTTP " + resp.status));
          err.payload = body;
          err.status = resp.status;
          throw err;
        }
        return body;
      });
    });
  }

  function postJSON(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (resp) {
      return resp.json().then(function (body) {
        if (!resp.ok || body.ok === false) {
          var err = new Error(body.error || ("HTTP " + resp.status));
          err.payload = body;
          err.status = resp.status;
          throw err;
        }
        return body;
      });
    });
  }

  // ---------- 文件清单 ----------
  function loadFiles() {
    setStatus("加载文件清单…");
    el.tree.innerHTML = "";
    getJSON("/api/files").then(function (data) {
      state.data = data;
      state.config = data.config || state.config;
      renderStats(data);
      renderTree();
      setStatus("就绪 · 点击左侧文件查看信息", "ok");
    }).catch(function (err) {
      state.data = err.payload || { ok: false, error: err.message, config: state.config };
      renderStats(state.data);
      renderTree();
      setStatus("产物不可用：" + err.message, "err");
      showToast("切块产物不可用，请在设置中检查路径", "err");
    });
  }

  function renderStats(data) {
    if (!data || data.ok === false) {
      el.stats.innerHTML = "<span style='color:var(--err)'>产物不可用</span><br>" +
        escapeHtml(data && data.error ? data.error : "未知错误");
      return;
    }
    var totals = data.totals || {};
    var lines = [];
    lines.push("文件 <b>" + fmtInt(totals.n_files) + "</b> · 切块 <b>" + fmtInt(totals.n_chunks) + "</b>");
    if (totals.kinds && typeof totals.kinds === "object") {
      var kinds = Object.keys(totals.kinds).map(function (k) { return k + " " + fmtInt(totals.kinds[k]); });
      lines.push("kind: " + kinds.join(" / "));
    } else if (totals.char_coverage !== undefined) {
      lines.push("覆盖率 " + totals.char_coverage);
    }
    lines.push("生成时间 " + escapeHtml(data.generated_at || "-"));
    el.stats.innerHTML = lines.join("<br>");
  }

  function matchFile(group, file) {
    if (!state.filter) return true;
    var q = state.filter.toLowerCase();
    return (file.law_name || "").toLowerCase().indexOf(q) >= 0 ||
      (file.key || "").toLowerCase().indexOf(q) >= 0 ||
      (group || "").toLowerCase().indexOf(q) >= 0;
  }

  function renderTree() {
    el.tree.innerHTML = "";
    var data = state.data;
    if (!data || data.ok === false) {
      el.tree.innerHTML = "<div class='empty-hint'>没有可显示的文件。<br>点击右上角「⚙ 设置」修正产物目录。</div>";
      return;
    }
    var groups = data.groups || [];
    var shown = 0;
    groups.forEach(function (group) {
      var files = (group.files || []).filter(function (f) { return matchFile(group.name, f); });
      if (!files.length) return;
      shown += files.length;

      var section = document.createElement("section");
      section.className = "group";

      var head = document.createElement("div");
      head.className = "group-head";
      head.innerHTML = "<span>" + escapeHtml(group.name) + "</span>" +
        "<span class='count'>" + files.length + " / " + (group.files || []).length + " 份</span>";
      section.appendChild(head);

      files.forEach(function (file) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "file";
        if (state.selected && state.selected.key === file.key) btn.className += " active";
        btn.dataset.key = file.key;

        var badges = [];
        if (file.gate_ok === false) badges.push("<span class='badge badge-err'>gate ✗</span>");
        else if (file.gate_ok === true) badges.push("<span class='badge badge-ok'>gate ✓</span>");
        if (file.n_warnings > 0) badges.push("<span class='badge badge-warn'>warning " + file.n_warnings + "</span>");

        btn.innerHTML =
          "<span class='name'>" + escapeHtml(file.law_name) + "</span>" +
          "<span class='meta'><span>切块 " + fmtInt(file.n_chunks) + "</span>" +
          badges.join("") + "</span>";
        btn.addEventListener("click", function () { selectFile(file); });
        section.appendChild(btn);
      });

      el.tree.appendChild(section);
    });

    if (!shown) {
      el.tree.innerHTML = "<div class='empty-hint'>没有匹配的文件</div>";
    }
  }

  function selectFile(file) {
    state.selected = file;
    renderTree();
    renderDetail(file);
  }

  function renderDetail(file) {
    var rows = [
      ["key", file.key],
      ["分组", state.selectedGroupName(file.key)],
      ["法规名", file.law_name],
      ["切块数", fmtInt(file.n_chunks)],
      ["chunk jsonl", file.jsonl],
      ["源文件", file.source_path || "（缺失）"],
      ["sha256", file.sha256 || "-"],
      ["kind 分布", file.kinds ? Object.keys(file.kinds).map(function (k) { return k + " " + file.kinds[k]; }).join(", ") : "-"],
      ["字符覆盖率", file.char_coverage !== undefined ? file.char_coverage : "-"],
      ["extras", fmtInt(file.extras_count)],
      ["warnings", file.n_warnings !== undefined ? fmtInt(file.n_warnings) : "-"]
    ];
    var kv = rows.map(function (r) {
      return "<dt>" + escapeHtml(r[0]) + "</dt><dd>" + escapeHtml(String(r[1] === undefined ? "-" : r[1])) + "</dd>";
    }).join("");

    el.detail.innerHTML =
      "<div class='file-card'>" +
      "<h2>" + escapeHtml(file.law_name) + "</h2>" +
      "<div class='sub'>" + escapeHtml(file.group || "") + " · " + escapeHtml(file.key) + "</div>" +
      "<dl class='kv'>" + kv + "</dl>" +
      "<div class='notice'>P0 只有文件清单；<b>P1</b> 会在这里显示「Markdown 原文 ↔ 切片」三栏联动。当前请先确认这份文件与切块数是否正确。</div>" +
      "</div>";
  }

  function selectedGroupName(key) {
    var groups = (state.data && state.data.groups) || [];
    for (var i = 0; i < groups.length; i++) {
      var files = groups[i].files || [];
      for (var j = 0; j < files.length; j++) {
        if (files[j].key === key) return groups[i].name;
      }
    }
    return "-";
  }

  // ---------- 设置 ----------
  function openSettings() {
    var cfg = (state.data && state.data.ok !== false) ? (state.data.config || state.config) : (state.config || {});
    if (!cfg) cfg = {};
    if (cfg.ok === false) cfg = cfg.config || cfg;
    el.chunksDir.value = cfg.chunks_dir || "";
    el.facRoot.value = cfg.fac_root || "";
    el.port.value = cfg.port || 8790;
    renderSourceRoots(cfg);
    el.cfgErrors.hidden = true;
    el.cfgErrors.textContent = "";
    el.dialog.showModal();
  }

  function renderSourceRoots(cfg) {
    el.sourceRoots.innerHTML = "";
    var defaults = cfg.default_source_roots || {};
    var configured = cfg.source_roots || {};
    var names = Object.keys(configured);
    Object.keys(defaults).forEach(function (name) {
      if (names.indexOf(name) < 0) names.push(name);
    });
    (cfg.groups || []).forEach(function (name) {
      if (names.indexOf(name) < 0) names.push(name);
    });
    if (!names.length) {
      el.sourceRoots.innerHTML = "<div class='hint'>（暂无分组信息，先保存产物目录）</div>";
      return;
    }
    names.forEach(function (name) {
      var row = document.createElement("div");
      row.className = "source-row";
      var label = document.createElement("label");
      label.textContent = name;
      var input = document.createElement("input");
      input.type = "text";
      input.dataset.group = name;
      input.placeholder = defaults[name] || "留空自动推导（默认：" + (defaults[name] || "未知") + "）";
      input.value = configured[name] || "";
      input.spellcheck = false;
      row.appendChild(label);
      row.appendChild(input);
      el.sourceRoots.appendChild(row);
    });
  }

  function collectSettings() {
    var sourceRoots = {};
    Array.prototype.forEach.call(el.sourceRoots.querySelectorAll("input[data-group]"), function (input) {
      var v = input.value.trim();
      if (v) sourceRoots[input.dataset.group] = v;
    });
    var patch = {
      chunks_dir: el.chunksDir.value.trim(),
      fac_root: el.facRoot.value.trim(),
      port: parseInt(el.port.value, 10) || 8790,
      source_roots: sourceRoots
    };
    return patch;
  }

  function saveSettings(ev) {
    if (ev) ev.preventDefault();
    var patch = collectSettings();
    var btn = document.getElementById("btn-save");
    btn.disabled = true;
    btn.textContent = "保存中…";
    postJSON("/api/config", patch).then(function (body) {
      state.config = body.config;
      el.dialog.close();
      showToast(body.message || "配置已保存", "ok");
      loadFiles();
    }).catch(function (err) {
      el.cfgErrors.hidden = false;
      el.cfgErrors.textContent = err.message;
      showToast("保存失败：" + err.message, "err");
    }).finally(function () {
      btn.disabled = false;
      btn.textContent = "保存";
    });
  }

  // ---------- 其他 ----------
  function escapeHtml(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function initHealth() {
    getJSON("/api/health").then(function (h) {
      if (h.phase) el.phase.textContent = h.phase;
    }).catch(function () { /* 忽略：文件清单会暴露问题 */ });
  }

  function bindEvents() {
    el.search.addEventListener("input", function () {
      state.filter = el.search.value.trim();
      renderTree();
    });
    el.btnRefresh.addEventListener("click", loadFiles);
    el.btnSettings.addEventListener("click", openSettings);
    el.btnCancel.addEventListener("click", function () { el.dialog.close(); });
    el.form.addEventListener("submit", saveSettings);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && el.dialog.open) el.dialog.close();
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
        ev.preventDefault();
        el.search.focus();
      }
    });
  }

  bindEvents();
  initHealth();
  loadFiles();
})();
