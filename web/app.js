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
    selRange: null,    // 当前选区行范围 {start, end}（1-based 闭区间）
    pendingChunk: null,// 当前弹层操作的切片
    addAfterId: null,  // 新增切片的插入位置（null = 末尾）
    addSpan: null      // 新增切片来自原文选区时的行区间 [start, end]（1-based 闭区间）
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
    btnUndo: document.getElementById("btn-undo"),
    btnRedo: document.getElementById("btn-redo"),
    dirty: document.getElementById("dirty"),
    diff: document.getElementById("diff"),
    btnAdd: document.getElementById("btn-add"),
    btnReset: document.getElementById("btn-reset"),
    chunksHint: document.getElementById("chunks-hint"),
    splitDialog: document.getElementById("split-dialog"),
    splitForm: document.getElementById("split-form"),
    splitTarget: document.getElementById("split-target"),
    splitLines: document.getElementById("split-lines"),
    splitCharRow: document.getElementById("split-char-row"),
    splitChar: document.getElementById("split-char"),
    splitErrors: document.getElementById("split-errors"),
    editDialog: document.getElementById("edit-dialog"),
    editForm: document.getElementById("edit-form"),
    editTarget: document.getElementById("edit-target"),
    editText: document.getElementById("edit-text"),
    editLawNum: document.getElementById("edit-law-num"),
    editKind: document.getElementById("edit-kind"),
    editErrors: document.getElementById("edit-errors"),
    addDialog: document.getElementById("add-dialog"),
    addHint: document.getElementById("add-hint"),
    addForm: document.getElementById("add-form"),
    addText: document.getElementById("add-text"),
    addLawNum: document.getElementById("add-law-num"),
    addKind: document.getElementById("add-kind"),
    addErrors: document.getElementById("add-errors"),
    dialog: document.getElementById("settings-dialog"),
    form: document.getElementById("settings-form"),
    chunksDir: document.getElementById("cfg-chunks-dir"),
    facRoot: document.getElementById("cfg-fac-root"),
    port: document.getElementById("cfg-port"),
    sourceRoots: document.getElementById("cfg-source-roots"),
    cfgErrors: document.getElementById("cfg-errors"),
    btnCancel: document.getElementById("btn-cancel"),
    toast: document.getElementById("toast"),
    ctxMenu: document.getElementById("ctx-menu")
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
      syncEditUI(doc);
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

    // 切片范围条：每块的行区间分配到 4 条泳道（贪心：互不重叠的块共用一条）
    var MAX_LANES = 4;
    var laneEnds = [];
    var laneOfChunk = new Array(doc.chunks.length);
    doc.chunks.forEach(function (chunk, idx) {
      if (!chunk.span) return;
      var start = chunk.span[0];
      var end = chunk.span[1];
      var lane = -1;
      for (var l = 0; l < laneEnds.length; l++) {
        if (laneEnds[l] < start) { lane = l; break; }
      }
      if (lane < 0) {
        if (laneEnds.length < MAX_LANES) { lane = laneEnds.length; laneEnds.push(end); }
        else { lane = MAX_LANES - 1; }
      }
      laneEnds[lane] = Math.max(laneEnds[lane] || 0, end);
      laneOfChunk[idx] = lane;
    });
    var laneBuckets = new Array(total + 1);
    for (var b = 1; b <= total; b++) laneBuckets[b] = {};
    doc.chunks.forEach(function (chunk, idx) {
      if (!chunk.span) return;
      var lane = laneOfChunk[idx];
      for (var k = chunk.span[0]; k <= chunk.span[1]; k++) {
        var bucket = laneBuckets[k];
        if (!bucket) continue;
        var cell = bucket[lane];
        if (!cell) cell = bucket[lane] = { lane: lane, chunks: [], start: false, end: false };
        cell.chunks.push(idx);
        if (chunk.span[0] === k) cell.start = true;
        if (chunk.span[1] === k) cell.end = true;
      }
    });

    for (var j = 0; j < total; j++) {
      var line = doc.lines[j];
      var row = document.createElement("div");
      row.className = "line kind-" + line.kind;
      row.dataset.line = String(line.n);
      var no = document.createElement("span");
      no.className = "no";
      no.textContent = String(line.n);
      var lanes = document.createElement("span");
      lanes.className = "lanes";
      var buckets = laneBuckets[line.n] || {};
      Object.keys(buckets).forEach(function (key) {
        var cell = buckets[key];
        var bar = document.createElement("span");
        bar.className = "lane lane-" + cell.lane;
        if (cell.start) bar.className += " start";
        if (cell.end) bar.className += " end";
        if (cell.start && cell.end) bar.className += " only";
        bar.dataset.chunk = String(cell.chunks[0]);
        if (cell.chunks.length === 1) {
          var only = doc.chunks[cell.chunks[0]];
          bar.title = "切片 #" + (cell.chunks[0] + 1) + " · " + only.id + " · 第 " + only.span[0] + "–" + only.span[1] + " 行";
        } else {
          var ids = cell.chunks.slice(0, 4).map(function (idx) { return doc.chunks[idx].id; }).join(", ");
          bar.title = "第 " + line.n + " 行被 " + cell.chunks.length + " 个切片覆盖：" + ids +
            (cell.chunks.length > 4 ? " …" : "");
        }
        lanes.appendChild(bar);
      });
      var txt = document.createElement("span");
      txt.className = "txt";
      if (line.text === "") {
        row.className += " blank";
      } else {
        txt.textContent = line.text;
      }
      if (line.truncated) row.className += " truncated";
      row.appendChild(no);
      row.appendChild(lanes);
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
      if (chunk.manual) badges.push("<span class='badge badge-manual'>手工</span>");
      head.innerHTML = "<span class='chunk-seq'>#" + (index + 1) + "</span>" + badges.join("");
      card.appendChild(head);

      var body = document.createElement("pre");
      body.className = "chunk-body";
      body.textContent = chunk.chunk;
      card.appendChild(body);

      var foot = document.createElement("div");
      foot.className = "chunk-foot";
      var spanText = chunk.span ? "第 " + chunk.span[0] + "–" + chunk.span[1] + " 行" : "未对齐";
      foot.innerHTML = "<span>" + escapeHtml(chunk.id) + "</span>" +
        "<span class='chunk-span'>" + spanText + "</span>" +
        "<span>" + fmtInt(chunk.token_len) + " 字符</span>";
      card.appendChild(foot);

      var ops = document.createElement("div");
      ops.className = "chunk-ops";
      var isFirst = index === 0;
      var isLast = index === doc.chunks.length - 1;
      var actions = [
        { act: "split", label: "✂ 拆", title: "把这块拆成两块（Ctrl+Enter）" },
        { act: "merge-prev", label: "⤴ 并↑", title: "与上一块合并", disabled: isFirst },
        { act: "merge-next", label: "⤵ 并↓", title: "与下一块合并", disabled: isLast },
        { act: "drop", label: "✕ 删", title: "删除这块（Delete）" },
        { act: "edit", label: "✎ 改", title: "编辑正文 / 条号 / 类型" }
      ];
      actions.forEach(function (action) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "chip";
        btn.textContent = action.label;
        btn.title = action.title;
        if (action.disabled) btn.disabled = true;
        btn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          if (action.act === "split") openSplit(chunk);
          else if (action.act === "merge-prev") mergeChunk(chunk, "prev");
          else if (action.act === "merge-next") mergeChunk(chunk, "next");
          else if (action.act === "drop") dropChunk(chunk);
          else if (action.act === "edit") openEdit(chunk);
        });
        ops.appendChild(btn);
      });
      card.appendChild(ops);

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

  // 选区内部：把一个 DOM 位置换算成「该行正文里的字符偏移」
  function lineCharOffset(row, node, offset) {
    if (!row) return 0;
    var txt = row.querySelector(".txt");
    if (!txt) return 0;
    if (!node || !txt.contains(node)) return 0;
    var base = 0;
    var walker = document.createTreeWalker(txt, NodeFilter.SHOW_TEXT);
    var current;
    while ((current = walker.nextNode())) {
      if (current === node) return base + Math.min(offset, current.textContent.length);
      base += current.textContent.length;
    }
    return base;
  }

  // 选区 → { start, end, text }：行范围 + 按源行重建的纯文本（不含行号/泳道）
  function selectedText() {
    var sel = window.getSelection ? window.getSelection() : null;
    if (!sel || sel.isCollapsed || !sel.rangeCount || !state.doc) return null;
    var range = sel.getRangeAt(0);
    if (!range || !el.source.contains(range.startContainer) || !el.source.contains(range.endContainer)) {
      return null;
    }
    var p1 = { line: lineOfNode(range.startContainer), node: range.startContainer, offset: range.startOffset };
    var p2 = { line: lineOfNode(range.endContainer), node: range.endContainer, offset: range.endOffset };
    if (!p1.line || !p2.line) return null;
    if (p1.line > p2.line) { var tmp = p1; p1 = p2; p2 = tmp; }
    var a = p1.line;
    var b = p2.line;
    var startOff = lineCharOffset(state.lineEls[a - 1], p1.node, p1.offset);
    var endOff = lineCharOffset(state.lineEls[b - 1], p2.node, p2.offset);
    if (a === b && startOff > endOff) { var swap = startOff; startOff = endOff; endOff = swap; }
    var lines = state.doc.lines || [];
    var parts = [];
    if (a === b) {
      parts.push((lines[a - 1].text || "").slice(startOff, endOff));
    } else {
      parts.push((lines[a - 1].text || "").slice(startOff));
      for (var n = a + 1; n < b; n++) parts.push(lines[n - 1].text || "");
      parts.push((lines[b - 1].text || "").slice(0, endOff));
    }
    var text = parts.join("\n");
    return { start: a, end: b, text: text };
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
      var bar = ev.target.closest(".lane");
      if (bar && bar.dataset.chunk !== undefined) {
        var hit = state.doc && state.doc.chunks[parseInt(bar.dataset.chunk, 10)];
        if (hit) {
          selectChunk(hit, { scrollSource: false, scrollCard: true });
          return;
        }
      }
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
    // 右键：在原文选区/切片上弹出上下文工作栏
    el.source.addEventListener("contextmenu", openContextMenu);
  }

  // ---------- 右键上下文菜单：原文选区 ↔ 切片操作 ----------
  function chunkAtLine(line) {
    var doc = state.doc;
    if (!doc || !line) return null;
    for (var i = 0; i < doc.chunks.length; i++) {
      var c = doc.chunks[i];
      if (c.span && c.span[0] <= line && line <= c.span[1]) return c;
    }
    return null;
  }

  function hideCtxMenu() {
    if (!el.ctxMenu || el.ctxMenu.hidden) return;
    el.ctxMenu.hidden = true;
    el.ctxMenu.innerHTML = "";
  }

  function ctxItem(label, hint, action, disabled) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ctx-item";
    btn.setAttribute("role", "menuitem");
    var main = document.createElement("span");
    main.className = "ctx-label";
    main.textContent = label;
    btn.appendChild(main);
    if (hint) {
      var small = document.createElement("span");
      small.className = "ctx-hint";
      small.textContent = hint;
      btn.appendChild(small);
    }
    btn.disabled = !!disabled;
    btn.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      hideCtxMenu();
      action();
    });
    return btn;
  }

  function ctxSep() {
    var sep = document.createElement("div");
    sep.className = "ctx-sep";
    return sep;
  }

  function openContextMenu(ev) {
    if (!state.doc) return;
    var row = ev.target && ev.target.closest ? ev.target.closest(".line") : null;
    var picked = selectedText();
    var line = row && row.dataset.line ? parseInt(row.dataset.line, 10) : 0;
    if (!line && picked) line = picked.start;
    var target = chunkAtLine(line);
    if (!target && picked) {
      var hits = chunksInLineRange(picked.start, picked.end);
      target = hits.length ? hits[0] : null;
    }

    el.ctxMenu.innerHTML = "";
    var count = 0;
    var doc = state.doc;

    if (picked && picked.text.trim()) {
      var rangeLabel = picked.start === picked.end
        ? ("第 " + picked.start + " 行")
        : ("第 " + picked.start + "–" + picked.end + " 行");
      el.ctxMenu.appendChild(ctxItem("✚ 用选区新增切片", rangeLabel + " · " + picked.text.length + " 字", function () {
        openAdd(picked);
      }));
      count++;
    }

    if (target) {
      var idx = doc.chunks.indexOf(target);
      if (count) el.ctxMenu.appendChild(ctxSep());
      el.ctxMenu.appendChild(ctxItem("✂ 拆分此切片…", target.id, function () { openSplit(target); }));
      el.ctxMenu.appendChild(ctxItem("✎ 修改正文 / 条号…", target.id, function () { openEdit(target); }));
      el.ctxMenu.appendChild(ctxItem("⤴ 并入上一块", "", function () { mergeChunk(target, "prev"); }, idx <= 0));
      el.ctxMenu.appendChild(ctxItem("⤵ 并入下一块", "", function () { mergeChunk(target, "next"); }, idx >= doc.chunks.length - 1));
      el.ctxMenu.appendChild(ctxItem("✕ 删除此切片", target.id, function () { dropChunk(target); }));
      el.ctxMenu.appendChild(ctxSep());
      el.ctxMenu.appendChild(ctxItem("◎ 定位到切片", target.span
        ? ("原文第 " + target.span[0] + "–" + target.span[1] + " 行")
        : "未对齐", function () {
        selectChunk(target, { scrollSource: true, scrollCard: true });
      }));
      count++;
    }

    if (!count) { hideCtxMenu(); return; }
    ev.preventDefault();
    el.ctxMenu.hidden = false;
    el.ctxMenu.style.left = "0px";
    el.ctxMenu.style.top = "0px";
    var w = el.ctxMenu.offsetWidth || 230;
    var h = el.ctxMenu.offsetHeight || 280;
    var x = ev.clientX || 0;
    var y = ev.clientY || 0;
    var maxX = (window.innerWidth || 1280) - w - 8;
    var maxY = (window.innerHeight || 800) - h - 8;
    if (x > maxX) x = Math.max(8, maxX);
    if (y > maxY) y = Math.max(8, maxY);
    el.ctxMenu.style.left = x + "px";
    el.ctxMenu.style.top = y + "px";
  }

  // ---------- P2：人工编辑（拆 / 并 / 删 / 增 / 改 + 撤销重做） ----------
  var CHUNK_KINDS = ["clause", "table", "appendix", "paragraph", "stub", "front", "title"];

  function fillKind(select, value) {
    select.innerHTML = "";
    CHUNK_KINDS.forEach(function (kind) {
      var opt = document.createElement("option");
      opt.value = kind;
      opt.textContent = kind + "（" + (KIND_LABEL[kind] || kind) + "）";
      select.appendChild(opt);
    });
    select.value = CHUNK_KINDS.indexOf(value) >= 0 ? value : "paragraph";
  }

  function syncEditUI(doc) {
    var edits = (doc && doc.edits) || { n_ops: 0, undo: 0, redo: 0 };
    var diff = (doc && doc.diff) || { added: 0, modified: 0, removed: 0 };
    var dirty = !!(doc && doc.dirty);
    el.btnUndo.disabled = !edits.undo;
    el.btnRedo.disabled = !edits.redo;
    el.diff.textContent = "+" + diff.added + " ~" + diff.modified + " -" + diff.removed;
    el.diff.classList.toggle("editing", dirty);
    el.dirty.hidden = !dirty;
    el.chunksHint.hidden = !dirty;
    el.btnReset.disabled = !dirty;
    el.chunksHint.textContent = dirty
      ? ("手工修改只在内存里（共 " + edits.n_ops + " 条命令，可撤销）；刷新页面不丢，关服务才丢；P3 才落盘。")
      : "";
  }

  // 编辑后重渲染，尽量保持滚动位置与选中切片
  function applyDocPayload(doc) {
    var activeId = state.activeChunk;
    var sourceTop = el.source.scrollTop;
    var chunksTop = el.chunks.scrollTop;
    state.doc = doc;
    state.docKey = doc.key;
    state.activeChunk = null;
    renderDocument(doc);
    syncEditUI(doc);
    el.source.scrollTop = sourceTop;
    el.chunks.scrollTop = chunksTop;
    if (activeId) {
      var chunk = findChunk(activeId);
      if (chunk) selectChunk(chunk, { scrollSource: false, scrollCard: false });
    }
    setStatus("已应用编辑 · " + fmtInt(doc.stats.n_chunks) + " 块 · 未保存（P3 落盘）", "ok");
  }

  function opFailed(err, errEl) {
    var msg = "操作失败：" + err.message;
    if (errEl) {
      errEl.hidden = false;
      errEl.textContent = err.message;
      return;
    }
    showToast(msg, "err");
    setStatus(msg, "err");
  }

  function postOp(op, okMsg) {
    if (!state.docKey) return;
    postJSON("/api/op", { key: state.docKey, op: op }).then(function (doc) {
      applyDocPayload(doc);
      if (okMsg) showToast(okMsg, "ok");
    }).catch(function (err) {
      opFailed(err, state.opErrEl);
    });
  }

  function postEditCmd(url, okMsg) {
    if (!state.docKey) return;
    postJSON(url, { key: state.docKey }).then(function (doc) {
      applyDocPayload(doc);
      if (okMsg) showToast(okMsg, "ok");
    }).catch(function (err) {
      opFailed(err);
    });
  }

  function doUndo() {
    if (!state.doc || !state.doc.edits || !state.doc.edits.undo) return;
    postEditCmd("/api/undo", "已撤销");
  }

  function doRedo() {
    if (!state.doc || !state.doc.edits || !state.doc.edits.redo) return;
    postEditCmd("/api/redo", "已重做");
  }

  function doReset() {
    if (!state.doc || !state.doc.dirty) return;
    if (!window.confirm("丢弃本文件的全部手工修改，回到自动切块结果？")) return;
    postEditCmd("/api/reset", "已重置到自动切块结果");
  }

  function dropChunk(chunk) {
    if (!chunk) return;
    state.activeChunk = null;
    postOp({ op: "drop", id: chunk.id }, "已删除 " + chunk.id + "（Ctrl+Z 可撤销）");
  }

  function mergeChunk(chunk, direction) {
    if (!state.doc) return;
    var chunks = state.doc.chunks;
    var index = chunks.indexOf(chunk);
    var withChunk = direction === "prev" ? chunks[index - 1] : chunks[index + 1];
    if (!withChunk) return;
    postOp({ op: "merge", id: chunk.id, with: withChunk.id }, "已合并 " + chunk.id + " 与 " + withChunk.id);
  }

  function openSplit(chunk) {
    if (!chunk) return;
    var body = String(chunk.chunk || "");
    var lines = body.split("\n");
    state.pendingChunk = chunk;
    state.opErrEl = el.splitErrors;
    el.splitErrors.hidden = true;
    el.splitErrors.textContent = "";
    el.splitTarget.textContent = chunk.id + " · " + (KIND_LABEL[chunk.chunk_kind] || chunk.chunk_kind);
    el.splitLines.innerHTML = "";
    if (lines.length > 1) {
      el.splitCharRow.hidden = true;
      for (var k = 1; k < lines.length; k++) {
        var label = document.createElement("label");
        label.className = "split-line-opt";
        var radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "split-at";
        radio.value = String(k);
        if (k === 1) radio.checked = true;
        var text = document.createElement("span");
        text.textContent = "在第 " + k + " 行之后切开（上半 " + k + " 行，下半 " + (lines.length - k) + " 行）";
        label.appendChild(radio);
        label.appendChild(text);
        el.splitLines.appendChild(label);
      }
    } else {
      el.splitCharRow.hidden = false;
      el.splitLines.innerHTML = "<div class='hint'>本块只有一行，按字符位置切开。</div>";
      var mid = Math.max(1, Math.floor(body.length / 2));
      el.splitChar.value = String(mid);
      el.splitChar.max = String(Math.max(1, body.length - 1));
    }
    el.splitDialog.showModal();
  }

  function submitSplit(ev) {
    ev.preventDefault();
    var chunk = state.pendingChunk;
    if (!chunk) return;
    var op = { op: "split", id: chunk.id };
    var lines = String(chunk.chunk || "").split("\n");
    if (lines.length > 1) {
      var checked = el.splitLines.querySelector("input[name=split-at]:checked");
      if (!checked) return;
      op.at_line = parseInt(checked.value, 10);
    } else {
      var atChar = parseInt(el.splitChar.value, 10);
      if (!isFinite(atChar)) { opFailed({ message: "请输入切分字符位置" }, el.splitErrors); return; }
      op.at_char = atChar;
    }
    postJSON("/api/op", { key: state.docKey, op: op }).then(function (doc) {
      el.splitDialog.close();
      applyDocPayload(doc);
      showToast("已拆分 " + chunk.id, "ok");
    }).catch(function (err) {
      opFailed(err, el.splitErrors);
    });
  }

  function openEdit(chunk) {
    if (!chunk) return;
    state.pendingChunk = chunk;
    state.opErrEl = el.editErrors;
    el.editErrors.hidden = true;
    el.editErrors.textContent = "";
    el.editTarget.textContent = chunk.id + " · " + (chunk.span
      ? "原文第 " + chunk.span[0] + "–" + chunk.span[1] + " 行"
      : "未对齐");
    el.editText.value = chunk.chunk;
    el.editLawNum.value = chunk.law_num || "";
    fillKind(el.editKind, chunk.chunk_kind);
    el.editDialog.showModal();
  }

  function submitEdit(ev) {
    ev.preventDefault();
    var chunk = state.pendingChunk;
    if (!chunk) return;
    var op = { op: "patch", id: chunk.id };
    var text = el.editText.value;
    var lawNum = el.editLawNum.value.trim();
    var kind = el.editKind.value;
    if (text !== chunk.chunk) op.chunk = text;
    if (lawNum !== chunk.law_num) op.law_num = lawNum;
    if (kind !== chunk.chunk_kind) op.chunk_kind = kind;
    if (Object.keys(op).length === 1) {
      el.editDialog.close();
      showToast("没有修改", "warn");
      return;
    }
    postJSON("/api/op", { key: state.docKey, op: op }).then(function (doc) {
      el.editDialog.close();
      applyDocPayload(doc);
      showToast("已修改 " + chunk.id, "ok");
    }).catch(function (err) {
      opFailed(err, el.editErrors);
    });
  }

  function openAdd(picked) {
    if (!state.doc) { showToast("先打开一个文件", "warn"); return; }
    state.opErrEl = el.addErrors;
    el.addErrors.hidden = true;
    el.addErrors.textContent = "";

    // 如果原文里有选区（或右键菜单带入了选区）：把选区文本带进弹层，并记下行区间
    if (!picked) picked = selectedText();
    if (picked && picked.text.trim()) {
      state.addSpan = [picked.start, picked.end];
      el.addText.value = picked.text;
      var hits = chunksInLineRange(picked.start, picked.end);
      state.addAfterId = hits.length ? hits[hits.length - 1].id : (state.activeChunk || null);
      var rangeLabel = picked.start === picked.end
        ? "第 " + picked.start + " 行"
        : "第 " + picked.start + "–" + picked.end + " 行";
      el.addHint.textContent = "已带入原文" + rangeLabel + "的选区（" + picked.text.length +
        " 字），新增块会定位到这几行；可继续编辑正文。";
      el.addHint.hidden = false;
    } else {
      state.addSpan = null;
      el.addText.value = "";
      state.addAfterId = state.activeChunk || null;
      el.addHint.textContent = "先在中间原文里拖选一段文字，点「新增」可直接把选区变成切片；也可以直接输入正文手动新增。";
      el.addHint.hidden = false;
    }
    el.addLawNum.value = "";
    fillKind(el.addKind, "paragraph");
    el.addDialog.showModal();
  }

  function submitAdd(ev) {
    ev.preventDefault();
    var text = el.addText.value;
    if (!text.trim()) { opFailed({ message: "正文不能为空" }, el.addErrors); return; }
    var op = {
      op: "add",
      text: text,
      law_num: el.addLawNum.value.trim(),
      chunk_kind: el.addKind.value
    };
    if (state.addAfterId) op.after_id = state.addAfterId;
    if (state.addSpan) op.span = [state.addSpan[0], state.addSpan[1]];
    postJSON("/api/op", { key: state.docKey, op: op }).then(function (doc) {
      el.addDialog.close();
      var hadSpan = !!op.span;
      state.addSpan = null;
      applyDocPayload(doc);
      showToast(hadSpan ? "已按原文选区新增一块" : "已新增一块", "ok");
    }).catch(function (err) {
      opFailed(err, el.addErrors);
    });
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
  function applyLanesVisibility(on) {
    document.body.classList.toggle("no-lanes", !on);
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
    el.btnRefresh.addEventListener("click", function () {
      state.doc = null;
      state.docKey = null;
      loadFiles();
    });
    el.btnSettings.addEventListener("click", openSettings);
    el.btnCancel.addEventListener("click", function () { el.dialog.close(); });
    el.form.addEventListener("submit", saveSettings);
    var laneToggle = document.getElementById("toggle-lanes");
    if (laneToggle) {
      var stored = null;
      try { stored = window.localStorage.getItem("sca.lanes"); } catch (e) { stored = null; }
      laneToggle.checked = stored !== "0";
      applyLanesVisibility(laneToggle.checked);
      laneToggle.addEventListener("change", function () {
        applyLanesVisibility(laneToggle.checked);
        try { window.localStorage.setItem("sca.lanes", laneToggle.checked ? "1" : "0"); } catch (e) { /* 忽略 */ }
      });
    }
    // P2 编辑：按钮 + 弹层
    el.btnUndo.addEventListener("click", doUndo);
    el.btnRedo.addEventListener("click", doRedo);
    el.btnReset.addEventListener("click", doReset);
    el.btnAdd.addEventListener("click", openAdd);
    // 点「新增」按钮时不抢焦点/不清掉原文选区
    el.btnAdd.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
    el.splitForm.addEventListener("submit", submitSplit);
    el.editForm.addEventListener("submit", submitEdit);
    el.addForm.addEventListener("submit", submitAdd);
    Array.prototype.forEach.call(document.querySelectorAll(".btn-cancel-op"), function (btn) {
      btn.addEventListener("click", function () {
        [el.splitDialog, el.editDialog, el.addDialog].forEach(function (dlg) {
          if (dlg && dlg.open) dlg.close();
        });
      });
    });
    document.addEventListener("keydown", function (ev) {
      var tag = (ev.target && ev.target.tagName) || "";
      var typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
        !!(ev.target && ev.target.isContentEditable);
      var mod = ev.ctrlKey || ev.metaKey;
      var key = String(ev.key || "").toLowerCase();
      if (ev.key === "Escape") {
        hideCtxMenu();
        [el.dialog, el.splitDialog, el.editDialog, el.addDialog].forEach(function (dlg) {
          if (dlg && dlg.open) dlg.close();
        });
        return;
      }
      if (mod && key === "k") {
        ev.preventDefault();
        el.search.focus();
        return;
      }
      if (typing) return;
      if (mod && !ev.shiftKey && key === "z") {
        ev.preventDefault();
        doUndo();
        return;
      }
      if (mod && (key === "y" || (ev.shiftKey && key === "z"))) {
        ev.preventDefault();
        doRedo();
        return;
      }
      if (mod && ev.shiftKey && key === "n") {
        ev.preventDefault();
        openAdd();
        return;
      }
      if (mod && key === "enter" && state.activeChunk) {
        ev.preventDefault();
        openSplit(findChunk(state.activeChunk));
        return;
      }
      if (key === "delete" && state.activeChunk) {
        ev.preventDefault();
        dropChunk(findChunk(state.activeChunk));
      }
    });
    bindDocEvents();
    // 点菜单外部 / 滚动原文或切片 / 窗口尺寸变化时收起右键菜单
    document.addEventListener("click", function (ev) {
      if (!el.ctxMenu.hidden && !el.ctxMenu.contains(ev.target)) hideCtxMenu();
    });
    el.source.addEventListener("scroll", hideCtxMenu);
    el.chunks.addEventListener("scroll", hideCtxMenu);
    window.addEventListener("resize", hideCtxMenu);
    window.addEventListener("hashchange", function () {
      var key = keyFromHash();
      if (key && key !== state.docKey) selectByKey(key);
    });
  }

  bindEvents();
  initHealth();
  loadFiles();
})();
