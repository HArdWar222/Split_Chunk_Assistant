/* SplitChunkAssistant 前端（零依赖，手写 JS）—— P1：原文 ↔ 切片三栏联动 */
(function () {
  "use strict";

  var state = {
    data: null,        // /api/files 载荷
    config: null,      // /api/config 载荷
    filter: "",
    selected: null,    // 当前文件 entry
    doc: null,         // /api/doc 载荷
    docKey: null,
    activeChunk: null, // 当前选中 chunk id
    hoverChunk: null,  // 当前悬停 chunk id
    lineEls: [],       // 源行元素（下标 = 行号-1）
    lineChunks: null,  // 行号(1-based) -> [chunk, ...]
    cardEls: {},       // chunk id -> 卡片元素
    hlEls: [],         // 当前高亮的源行元素
    hoverLine: null,   // 当前悬停的源行号
    selHits: [],       // 当前选区命中的 chunk id
    selRange: null     // 当前选区行范围 {start, end}（1-based 闭区间）
  };

  var el = {
    status: document.getElementById("status"),
    stats: document.getElementById("stats"),
    tree: document.getElementById("tree"),
    search: document.getElementById("search"),
    phase: document.getElementById("phase"),
    sourceTitle: document.getElementById("source-title"),
    sourceMeta: document.getElementById("source-meta"),
    source: document.getElementById("source"),
    chunkCount: document.getElementById("chunk-count"),
    chunkMeta: document.getElementById("chunk-meta"),
    chunks: document.getElementById("chunks"),
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

  function escapeHtml(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
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
      setStatus("就绪 · 点击左侧文件查看原文与切片", "ok");
      selectByKey(keyFromHash());
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
    if (state.selected && state.selected.key === file.key && state.doc) return;
    state.selected = file;
    renderTree();
    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, "", "#doc=" + encodeURIComponent(file.key));
    }
    loadDoc(file);
  }

  function keyFromHash() {
    var hash = window.location.hash || "";
    if (hash.indexOf("#doc=") !== 0) return null;
    try {
      return decodeURIComponent(hash.slice(5));
    } catch (e) {
      return null;
    }
  }

  function selectByKey(key) {
    if (!key || !state.data || state.data.ok === false) return false;
    var groups = state.data.groups || [];
    for (var i = 0; i < groups.length; i++) {
      var files = groups[i].files || [];
      for (var j = 0; j < files.length; j++) {
        if (files[j].key === key) {
          selectFile(files[j]);
          return true;
        }
      }
    }
    return false;
  }

  // ---------- 文档：原文 + 切片 ----------
  function loadDoc(file) {
    var seq = (loadDoc._seq = (loadDoc._seq || 0) + 1);
    setStatus("加载 " + file.law_name + " …");
    el.source.innerHTML = "<div class='empty'><p>加载原文中…</p></div>";
    el.chunks.innerHTML = "<div class='empty'><p>加载切片中…</p></div>";
    el.chunkCount.textContent = "";
    el.chunkMeta.textContent = "";
    el.sourceTitle.textContent = file.law_name;
    el.sourceMeta.textContent = "读取源 Markdown 与切块产物…";

    getJSON("/api/doc?key=" + encodeURIComponent(file.key)).then(function (doc) {
      if (seq !== loadDoc._seq) return; // 快速切换文件时丢弃旧响应
      state.doc = doc;
      state.docKey = doc.key;
      state.activeChunk = null;
      state.hoverChunk = null;
      renderDocument(doc);
      setStatus("已加载 " + doc.law_name + " · " + fmtInt(doc.stats.n_chunks) + " 块 · 对齐 " +
        fmtInt(doc.stats.n_aligned) + "/" + fmtInt(doc.stats.n_chunks) + " (" +
        (doc.stats.align_rate * 100).toFixed(1) + "%)", "ok");
    }).catch(function (err) {
      if (seq !== loadDoc._seq) return;
      state.doc = null;
      var msg = err.status === 404 ? "源文件缺失或产物不可读：" + err.message : err.message;
      el.source.innerHTML = "<div class='empty'><h2>无法打开</h2><p>" + escapeHtml(msg) + "</p></div>";
      el.chunks.innerHTML = "<div class='empty'><p>—</p></div>";
      setStatus("加载失败：" + msg, "err");
      showToast(msg, "err");
    });
  }

  function renderDocument(doc) {
    state.selHits = [];
    state.selRange = null;
    renderSource(doc);
    renderChunks(doc);
    renderDocMeta(doc);
  }

  function renderSource(doc) {
    var frag = document.createDocumentFragment();
    var lineEls = [];
    var total = doc.lines.length;
    var lineChunks = new Array(total + 1); // 行号(1-based) -> [chunk, ...]
    for (var i = 0; i < total; i++) {
      lineChunks[i + 1] = [];
    }
    for (var c = 0; c < doc.chunks.length; c++) {
      var chunk = doc.chunks[c];
      if (!chunk.span) continue;
      for (var n = chunk.span[0]; n <= chunk.span[1]; n++) {
        if (lineChunks[n]) lineChunks[n].push(chunk);
      }
    }
    for (var j = 0; j < total; j++) {
      var line = doc.lines[j];
      var row = document.createElement("div");
      row.className = "line kind-" + line.kind;
      row.dataset.line = String(line.n);
      var no = document.createElement("span");
      no.className = "no";
      no.textContent = String(line.n);
      var txt = document.createElement("span");
      txt.className = "txt";
      if (line.text === "") {
        row.className += " blank";
      } else {
        txt.textContent = line.text;
      }
      if (line.truncated) row.className += " truncated";
      row.appendChild(no);
      row.appendChild(txt);
      frag.appendChild(row);
      lineEls.push(row);
    }
    el.source.innerHTML = "";
    el.source.appendChild(frag);
    state.lineEls = lineEls;
    state.lineChunks = lineChunks;
  }

  var KIND_LABEL = {
    clause: "条款", table: "表格", appendix: "附录", paragraph: "段落",
    stub: "空条", front: "文头", title: "标题"
  };
  var ALIGN_LABEL = {
    exact: "精确", substr: "包含匹配", table_rows: "表格行匹配",
    table_label: "表题定位", miss: "未对齐"
  };

  function renderChunks(doc) {
    var frag = document.createDocumentFragment();
    state.cardEls = {};
    doc.chunks.forEach(function (chunk, index) {
      var card = document.createElement("article");
      card.className = "chunk-card align-" + chunk.align;
      if (!chunk.span) card.className += " unaligned";
      card.dataset.id = chunk.id;
      card.dataset.index = String(index + 1);

      var head = document.createElement("div");
      head.className = "chunk-head";
      var badges = [];
      badges.push("<span class='badge badge-kind kind-" + escapeHtml(chunk.chunk_kind) + "'>" +
        escapeHtml(KIND_LABEL[chunk.chunk_kind] || chunk.chunk_kind) + "</span>");
      if (chunk.law_num) badges.push("<span class='badge badge-num'>" + escapeHtml(chunk.law_num) + "</span>");
      if (chunk.part_total > 1) {
        badges.push("<span class='badge badge-part'>part " + chunk.part_index + "/" + chunk.part_total + "</span>");
      }
      badges.push("<span class='badge badge-align " + escapeHtml(chunk.align) + "'>" +
        escapeHtml(ALIGN_LABEL[chunk.align] || chunk.align) + "</span>");
      head.innerHTML = "<span class='chunk-seq'>#" + (index + 1) + "</span>" + badges.join("");
      card.appendChild(head);

      var body = document.createElement("pre");
      body.className = "chunk-body";
      body.textContent = chunk.chunk;
      card.appendChild(body);

      var foot = document.createElement("div");
      foot.className = "chunk-foot";
      foot.innerHTML = "<span>" + escapeHtml(chunk.id) + "</span>" +
        "<span>" + fmtInt(chunk.token_len) + " 字符</span>";
      card.appendChild(foot);

      card.addEventListener("click", function () { selectChunk(chunk, { scrollSource: true, scrollCard: false }); });
      frag.appendChild(card);
      state.cardEls[chunk.id] = card;
    });
    el.chunks.innerHTML = "";
    el.chunks.appendChild(frag);
    el.chunkCount.textContent = "共 " + fmtInt(doc.stats.n_chunks) + " 块";
    el.chunkMeta.textContent = "对齐 " + fmtInt(doc.stats.n_aligned) + "/" + fmtInt(doc.stats.n_chunks) +
      " (" + (doc.stats.align_rate * 100).toFixed(1) + "%) · " + alignKindText(doc.stats.align_kinds);
  }

  function alignKindText(kinds) {
    if (!kinds) return "-";
    return Object.keys(kinds).map(function (k) {
      return (ALIGN_LABEL[k] || k) + " " + kinds[k];
    }).join(" / ");
  }

  function renderDocMeta(doc) {
    var meta = doc.manifest || {};
    el.sourceTitle.textContent = doc.law_name;
    el.sourceMeta.textContent = doc.group + " · " + fmtInt(doc.stats.n_lines) + " 行 · " +
      fmtInt(doc.source_chars) + " 字符 · gate " + (meta.gate_ok ? "✓" : "✗");
    el.sourceMeta.title = doc.source_path;
  }

  // ---------- 联动高亮 ----------
  function chunkLines(chunk) {
    // span 是 1-based 闭区间
    return chunk.span ? { start: chunk.span[0], end: chunk.span[1] } : null;
  }

  function highlightLines(chunk, strong) {
    clearLineHighlight();
    if (!chunk) return;
    var range = chunkLines(chunk);
    if (!range) return;
    var cls = strong ? "hl" : "hl-soft";
    for (var n = range.start; n <= range.end; n++) {
      var row = state.lineEls[n - 1];
      if (row) {
        row.classList.add(cls);
        state.hlEls.push(row);
      }
    }
  }

  function clearLineHighlight() {
    for (var i = 0; i < state.hlEls.length; i++) {
      state.hlEls[i].classList.remove("hl", "hl-soft");
    }
    state.hlEls = [];
  }

  function highlightRelatedCards(line) {
    var doc = state.doc;
    if (!doc) return [];
    if (state.cardEls._related) {
      state.cardEls._related.forEach(function (id) {
        if (state.cardEls[id]) state.cardEls[id].classList.remove("related");
      });
    }
    var related = (state.lineChunks && state.lineChunks[line]) || [];
    related.forEach(function (chunk) {
      if (state.cardEls[chunk.id]) state.cardEls[chunk.id].classList.add("related");
    });
    state.cardEls._related = related.map(function (c) { return c.id; });
    return related;
  }

  function clearRelatedCards() {
    var ids = (state.cardEls && state.cardEls._related) || [];
    ids.forEach(function (id) {
      if (state.cardEls[id]) state.cardEls[id].classList.remove("related");
    });
    if (state.cardEls) state.cardEls._related = [];
  }

  // ---------- 原文选区 → 切片映射 ----------
  function lineOfNode(node) {
    if (!node) return 0;
    var elNode = node.nodeType === 1 ? node : node.parentElement;
    if (!elNode || !elNode.closest) return 0;
    var row = elNode.closest(".line");
    if (!row || !row.dataset.line) return 0;
    var n = parseInt(row.dataset.line, 10);
    return isFinite(n) ? n : 0;
  }

  function selectedLineRange() {
    var sel = window.getSelection ? window.getSelection() : null;
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    var range = sel.getRangeAt(0);
    if (!range || !el.source.contains(range.startContainer) || !el.source.contains(range.endContainer)) {
      return null;
    }
    var a = lineOfNode(range.startContainer);
    var b = lineOfNode(range.endContainer);
    if (!a || !b) return null;
    if (a > b) { var t = a; a = b; b = t; }
    return { start: a, end: b };
  }

  function chunksInLineRange(start, end) {
    var doc = state.doc;
    if (!doc) return [];
    var hits = [];
    doc.chunks.forEach(function (chunk) {
      if (!chunk.span) return;
      if (chunk.span[1] >= start && chunk.span[0] <= end) hits.push(chunk);
    });
    return hits;
  }

  function clearSelectionMarks() {
    (state.selHits || []).forEach(function (id) {
      if (state.cardEls[id]) state.cardEls[id].classList.remove("hit");
    });
    if (state.selRange) {
      for (var n = state.selRange.start; n <= state.selRange.end; n++) {
        var row = state.lineEls[n - 1];
        if (row) row.classList.remove("sel");
      }
    }
    state.selHits = [];
    state.selRange = null;
  }

  function applySelectionNow() {
    var range = selectedLineRange();
    var hadMarks = (state.selHits && state.selHits.length) || state.selRange;
    clearSelectionMarks();
    if (!range) {
      if (hadMarks) setStatus("就绪 · 点击左侧文件查看原文与切片", "ok");
      return;
    }
    var hits = chunksInLineRange(range.start, range.end);
    state.selRange = range;
    state.selHits = hits.map(function (c) { return c.id; });
    for (var n = range.start; n <= range.end; n++) {
      var row = state.lineEls[n - 1];
      if (row) row.classList.add("sel");
    }
    hits.forEach(function (chunk) {
      if (state.cardEls[chunk.id]) state.cardEls[chunk.id].classList.add("hit");
    });
    if (!hits.length) {
      setStatus("原文第 " + range.start + "–" + range.end + " 行：没有对应的切片（该范围可能是未对齐块）", "warn");
      return;
    }
    var rangeText = range.start === range.end
      ? "第 " + range.start + " 行"
      : "第 " + range.start + "–" + range.end + " 行";
    setStatus("原文" + rangeText + " → 对应 " + hits.length + " 个切片（右侧已高亮）", "ok");
    // 若命中的卡片都不在可视区，滚到第一个
    var anyVisible = hits.some(function (chunk) {
      var card = state.cardEls[chunk.id];
      if (!card) return false;
      var top = card.offsetTop;
      return top + card.offsetHeight > el.chunks.scrollTop &&
        top < el.chunks.scrollTop + el.chunks.clientHeight;
    });
    if (!anyVisible) scrollToCard(hits[0].id);
  }

  function scheduleSelectionCheck() {
    window.clearTimeout(scheduleSelectionCheck._t);
    scheduleSelectionCheck._t = window.setTimeout(applySelectionNow, 120);
  }

  function scrollToLine(n) {
    var row = state.lineEls[n - 1];
    if (!row) return;
    var container = el.source;
    var top = row.offsetTop - container.clientHeight / 2 + row.offsetHeight / 2;
    container.scrollTop = Math.max(0, top);
  }

  function scrollToCard(chunkId) {
    var card = state.cardEls[chunkId];
    if (!card) return;
    var container = el.chunks;
    var top = card.offsetTop - container.clientHeight / 2 + card.offsetHeight / 2;
    container.scrollTop = Math.max(0, top);
  }

  function hoverChunk(chunk) {
    if (!chunk) {
      if (state.activeChunk) {
        highlightLines(findChunk(state.activeChunk), true);
      } else {
        clearLineHighlight();
      }
      return;
    }
    highlightLines(chunk, false);
  }

  function findChunk(chunkId) {
    if (!state.doc) return null;
    for (var i = 0; i < state.doc.chunks.length; i++) {
      if (state.doc.chunks[i].id === chunkId) return state.doc.chunks[i];
    }
    return null;
  }

  function selectChunk(chunk, opts) {
    opts = opts || {};
    if (state.activeChunk) {
      var prev = state.cardEls[state.activeChunk];
      if (prev) prev.classList.remove("active");
    }
    state.activeChunk = chunk.id;
    var card = state.cardEls[chunk.id];
    if (card) card.classList.add("active");
    highlightLines(chunk, true);
    if (opts.scrollSource && chunk.span) scrollToLine(chunk.span[0]);
    if (opts.scrollCard) scrollToCard(chunk.id);
    setStatus("已选中 " + chunk.id + " · " + (ALIGN_LABEL[chunk.align] || chunk.align) +
      (chunk.span ? " · 原文第 " + chunk.span[0] + "–" + chunk.span[1] + " 行" : " · 未对齐"), "ok");
  }

  function bindDocEvents() {
    el.chunks.addEventListener("mouseover", function (ev) {
      var card = ev.target.closest(".chunk-card");
      if (!card) return;
      if (state.hoverChunk === card.dataset.id) return;
      state.hoverChunk = card.dataset.id;
      var chunk = findChunk(card.dataset.id);
      if (chunk) hoverChunk(chunk);
    });
    el.chunks.addEventListener("mouseout", function (ev) {
      var card = ev.target.closest(".chunk-card");
      if (!card) return;
      if (ev.relatedTarget && card.contains(ev.relatedTarget)) return;
      state.hoverChunk = null;
      hoverChunk(null);
    });

    el.source.addEventListener("mouseover", function (ev) {
      var row = ev.target.closest(".line");
      if (!row) return;
      var line = parseInt(row.dataset.line, 10);
      if (state.hoverLine === line) return;
      state.hoverLine = line;
      highlightRelatedCards(line);
    });
    el.source.addEventListener("mouseout", function (ev) {
      var row = ev.target.closest(".line");
      if (!row) return;
      if (ev.relatedTarget && el.source.contains(ev.relatedTarget) && ev.relatedTarget.closest(".line")) return;
      state.hoverLine = null;
      clearRelatedCards();
    });
    el.source.addEventListener("click", function (ev) {
      var row = ev.target.closest(".line");
      if (!row) return;
      var line = parseInt(row.dataset.line, 10);
      var related = highlightRelatedCards(line);
      if (related && related.length) {
        selectChunk(related[0], { scrollSource: false, scrollCard: true });
      }
    });
    // 拖选原文文字：选区覆盖的行 → 高亮对应切片
    el.source.addEventListener("mouseup", function () {
      window.setTimeout(applySelectionNow, 0);
    });
    document.addEventListener("selectionchange", scheduleSelectionCheck);
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
      input.placeholder = "留空自动推导（默认：" + (defaults[name] || "未知") + "）";
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
    return {
      chunks_dir: el.chunksDir.value.trim(),
      fac_root: el.facRoot.value.trim(),
      port: parseInt(el.port.value, 10) || 8790,
      source_roots: sourceRoots
    };
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
    el.btnRefresh.addEventListener("click", function () {
      state.doc = null;
      state.docKey = null;
      loadFiles();
    });
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
    bindDocEvents();
    window.addEventListener("hashchange", function () {
      var key = keyFromHash();
      if (key && key !== state.docKey) selectByKey(key);
    });
  }

  bindEvents();
  initHealth();
  loadFiles();
})();
