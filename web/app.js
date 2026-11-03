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
    tables: null,      // /api/tables 载荷：chunk id -> table_grid（懒加载）
    tableBlocks: [],   // 原文里被渲染成表格的行区间 [{start, end, el}]
    tableRows: {},     // 渲染后的表格行：chunk id -> [tr, ...]（按 law_num「第X-Y行」映射）
    hlRows: [],        // 当前高亮的表格行
    rowActive: [],     // 当前选中切片对应的表格行
    pendingChunk: null,// 当前弹层操作的切片
    addAfterId: null,  // 新增切片的插入位置（null = 末尾）
    addBeforeId: null, // 新增切片插到某块之前（用于原文顶部缺口，null = 不用）
    addSpan: null,     // 新增切片来自原文选区时的行区间 [start, end]（1-based 闭区间）
    selTableRows: [],  // 当前选中的表格行（未切片/已切片都算，供「用这些行新增切片」）
    programmaticScroll: 0 // 最近一次程序化滚动的时刻（避免把菜单/光标误关）
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
    splitLawNum: document.getElementById("split-law-num"),
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
    splitCaret: document.getElementById("split-caret"),
    splitHint: document.getElementById("split-hint"),
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

  // ---------- 表格可视化（管道表 / HTML 表 / table_grid） ----------
  function isPipeLine(text) {
    var s = String(text || "").trim();
    return s.charAt(0) === "|" && s.indexOf("|", 1) >= 0;
  }

  function isSepLine(text) {
    var s = String(text || "").trim();
    return s.indexOf("-") >= 0 && /^\|?[\s:|\-]+\|?$/.test(s);
  }

  function splitPipeRow(text) {
    var s = String(text || "").trim();
    if (s.charAt(0) === "|") s = s.slice(1);
    if (s.charAt(s.length - 1) === "|") s = s.slice(0, -1);
    return s.split("|").map(function (cell) { return cell.trim(); });
  }

  function buildPipeTableHtml(lines, metaText) {
    var sep = -1;
    for (var i = 0; i < lines.length; i++) {
      if (isSepLine(lines[i])) { sep = i; break; }
    }
    if (sep <= 0) return null;
    var headers = splitPipeRow(lines[0]);
    var rows = [];
    for (var r = sep + 1; r < lines.length; r++) {
      if (!isPipeLine(lines[r])) break;
      rows.push(splitPipeRow(lines[r]));
    }
    var width = headers.length;
    rows.forEach(function (cells) { width = Math.max(width, cells.length); });
    // 单元格里常见 <br>（源 md 管道表里用 HTML 换行），转成真换行，不要原样显示标签
    function cellHtml(text) {
      return escapeHtml(text).replace(/&lt;br\s*\/?&gt;/gi, "<br>");
    }
    function rowHtml(cells, tag) {
      var out = [];
      for (var k = 0; k < width; k++) {
        out.push("<" + tag + ">" + cellHtml(cells[k] === undefined ? "" : cells[k]) + "</" + tag + ">");
      }
      return "<tr>" + out.join("") + "</tr>";
    }
    var meta = metaText || ("管道表 · " + rows.length + " 行 × " + width + " 列");
    return '<div class="tblmeta">' + escapeHtml(meta) + '</div>' +
      '<div class="tblwrap"><table class="grid-table"><thead>' + rowHtml(headers, "th") + '</thead><tbody>' +
      rows.map(function (cells) { return rowHtml(cells, "td"); }).join("") + '</tbody></table></div>';
  }

  function sanitizeTableNode(root) {
    var nodes = root.querySelectorAll ? root.querySelectorAll("*") : [];
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var tag = node.tagName ? node.tagName.toLowerCase() : "";
      if (tag === "script" || tag === "style" || tag === "iframe" || tag === "object" ||
          tag === "embed" || tag === "link" || tag === "meta" || tag === "form") {
        if (node.parentNode) node.parentNode.removeChild(node);
        continue;
      }
      var attrs = [];
      for (var a = 0; a < node.attributes.length; a++) attrs.push(node.attributes[a].name);
      attrs.forEach(function (name) {
        var lower = name.toLowerCase();
        var value = node.getAttribute(name) || "";
        if (lower.indexOf("on") === 0 || lower === "style" || lower === "src" ||
            (lower === "href" && /^\s*javascript:/i.test(value))) {
          node.removeAttribute(name);
        }
      });
    }
    return root;
  }

  var HTML_TABLE_RE = /<table\b[\s\S]*?<\/table>/gi;

  function buildHtmlTablesHtml(html, metaText) {
    if (!html || typeof DOMParser === "undefined") return null;
    var doc;
    try {
      doc = new DOMParser().parseFromString("<div>" + html + "</div>", "text/html");
    } catch (err) {
      return null;
    }
    var tables = doc.querySelectorAll("table");
    if (!tables.length) return null;
    var out = [];
    var meta = metaText || "";
    for (var i = 0; i < tables.length; i++) {
      var table = tables[i];
      var clone = table.cloneNode(true);
      clone.className = "grid-table";
      var holder = doc.createElement("div");
      holder.appendChild(clone);
      sanitizeTableNode(holder);
      var nRows = clone.querySelectorAll("tr").length;
      var firstRow = clone.querySelector("tr");
      var nCols = firstRow ? firstRow.children.length : 0;
      var label = meta || (i === 0 && tables.length === 1 ? "" : "HTML 表 #" + (i + 1));
      var head = '<div class="tblmeta">' + escapeHtml(
        (label ? label + " · " : "") + nRows + " 行 × " + nCols + " 列") + '</div>';
      out.push(head + '<div class="tblwrap">' + holder.innerHTML + '</div>');
    }
    return out.join("");
  }

  function buildGridTableHtml(table) {
    var headers = (table.headers || []).map(String);
    var rows = (table.rows || []).map(function (row) {
      return (row || []).map(String);
    });
    var width = Math.max(parseInt(table.width, 10) || 0, headers.length);
    rows.forEach(function (cells) { width = Math.max(width, cells.length); });
    if (!width) width = 1;
    while (headers.length < width) headers.push("");
    var headerRows = parseInt(table.header_rows, 10);
    if (!isFinite(headerRows)) headerRows = 1;
    var hasHeaders = headers.some(function (head) { return String(head).trim() !== ""; });
    var headHtml = hasHeaders
      ? "<thead><tr>" + headers.map(function (head) {
        return "<th>" + escapeHtml(head) + "</th>";
      }).join("") + "</tr></thead>"
      : "";
    var noteFrom = (table.note_from === undefined || table.note_from === null)
      ? rows.length : parseInt(table.note_from, 10);
    if (!isFinite(noteFrom)) noteFrom = rows.length;
    var bodyHtml = rows.map(function (cells, index) {
      while (cells.length < width) cells.push("");
      if (index >= noteFrom) {
        return '<tr class="noterow"><td colspan="' + width + '">' + escapeHtml(cells.join(" ").trim()) + '</td></tr>';
      }
      return "<tr>" + cells.map(function (cell) {
        return "<td>" + escapeHtml(cell) + "</td>";
      }).join("") + "</tr>";
    }).join("");
    var meta = [];
    if (table.meta_note) meta.push(escapeHtml(table.meta_note));
    if (table.table_label) meta.push(escapeHtml(table.table_label));
    if (table.table_title) meta.push(escapeHtml(table.table_title));
    meta.push((hasHeaders ? "表头 " + headerRows + " 行" : "无表头") + " · " + rows.length + " 行 × " + width + " 列");
    return '<div class="tblmeta">' + meta.join(" · ") + '</div>' +
      '<div class="tblwrap"><table class="grid-table">' + headHtml + '<tbody>' +
      bodyHtml + '</tbody></table></div>';
  }

  // 表格行的单元格与行在正文里以 \t / \n 分隔（后端 normalize_text 只去行尾空白，保留分隔符）。
  // 手工新增的表格块没有 table_grid，这里用正文还原一个只读网格供右侧展示。
  function inheritedTableHeaders(chunk, width) {
    if (!state.doc || !state.tables || !chunk.span) return null;
    var chunks = state.doc.chunks || [];
    for (var i = 0; i < chunks.length; i++) {
      var peer = chunks[i];
      if (peer.id === chunk.id || !peer.span) continue;
      if (peer.span[1] < chunk.span[0] || peer.span[0] > chunk.span[1]) continue;
      var grid = state.tables[peer.id];
      if (!grid || !grid.headers || !grid.headers.length) continue;
      if (grid.headers.length !== width) continue;
      var hasReal = grid.headers.some(function (head) { return String(head).trim() !== ""; });
      if (hasReal) return grid.headers.slice();
    }
    return null;
  }

  function manualTableGrid(chunk) {
    if (!chunk || !chunk.manual) return null;
    if (chunk.chunk_kind !== "table" && chunk.chunk_kind !== "appendix") return null;
    var raw = String(chunk.chunk === null || chunk.chunk === undefined ? "" : chunk.chunk);
    var lines = raw.split("\n").filter(function (line) { return line.trim() !== ""; });
    if (!lines.length) return null;
    var rows = lines.map(function (line) {
      return line.split("\t").map(function (cell) { return cell.trim(); });
    });
    var width = 0;
    rows.forEach(function (cells) { width = Math.max(width, cells.length); });
    if (width < 2) return null;
    var headers = inheritedTableHeaders(chunk, width);
    return {
      headers: headers || [],
      rows: rows,
      width: width,
      header_rows: headers ? 1 : 0,
      note_from: rows.length,
      meta_note: "手工表格 · " + (headers ? "表头继承自该表格" : "无表头（正文单元格拼接）")
    };
  }

  // 切片正文 → 可视化 HTML：优先 table_grid；否则按管道表 / HTML 表解析；纯文字回退 pre
  function chunkBodyHtml(chunk) {
    var raw = String(chunk.chunk === null || chunk.chunk === undefined ? "" : chunk.chunk);
    var tables = state.tables || {};
    var grid = chunk.manual ? manualTableGrid(chunk) : tables[chunk.id];
    if (grid) {
      return buildGridTableHtml(grid) +
        '<details class="chunk-raw"><summary>原始正文（送进检索的就是这段文字）</summary>' +
        '<pre class="chunk-raw-pre">' + escapeHtml(raw) + '</pre></details>';
    }

    var lines = raw.split("\n");
    var segments = [];
    var buffer = [];
    function flushText() {
      if (buffer.length) { segments.push({ type: "text", lines: buffer.slice() }); buffer = []; }
    }
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (/<table\b/i.test(line)) {
        flushText();
        var acc = [line];
        i++;
        while (i < lines.length && !/<\/table>/i.test(acc[acc.length - 1]) && acc.length < 400) {
          acc.push(lines[i]);
          i++;
        }
        segments.push({ type: "html", text: acc.join("\n") });
        continue;
      }
      if (isPipeLine(line)) {
        var block = [];
        while (i < lines.length && (isPipeLine(lines[i]) || isSepLine(lines[i]))) {
          block.push(lines[i]);
          i++;
        }
        var hasSep = block.some(isSepLine);
        if (hasSep && block.length >= 2) {
          flushText();
          segments.push({ type: "pipe", lines: block });
        } else {
          for (var q = 0; q < block.length; q++) buffer.push(block[q]);
        }
        continue;
      }
      buffer.push(line);
      i++;
    }
    flushText();

    var hasTable = segments.some(function (seg) { return seg.type !== "text"; });
    if (!hasTable) return '<pre class="chunk-body">' + escapeHtml(raw) + '</pre>';

    var html = segments.map(function (seg) {
      if (seg.type === "text") {
        return '<pre class="chunk-body">' + escapeHtml(seg.lines.join("\n")) + '</pre>';
      }
      if (seg.type === "pipe") {
        var pipe = buildPipeTableHtml(seg.lines);
        if (pipe) return pipe;
        return '<pre class="chunk-body">' + escapeHtml(seg.lines.join("\n")) + '</pre>';
      }
      var built = buildHtmlTablesHtml(seg.text);
      if (built) return built;
      return '<pre class="chunk-body">' + escapeHtml(seg.text) + '</pre>';
    }).join("");
    return html +
      '<details class="chunk-raw"><summary>原始正文（送进检索的就是这段文字）</summary>' +
      '<pre class="chunk-raw-pre">' + escapeHtml(raw) + '</pre></details>';
  }

  // 原文行 → 表格块：[{kind, start, end, lines?, text?}]（1-based 闭区间）
  function detectSourceTableBlocks(lines) {
    var blocks = [];
    var total = lines.length;
    var i = 0;
    while (i < total) {
      var text = lines[i].text || "";
      if (/<table\b/i.test(text)) {
        var end = i;
        if (!/<\/table>/i.test(text)) {
          var j = i + 1;
          while (j < total && j < i + 400 && !/<\/table>/i.test(lines[j].text || "")) j++;
          end = Math.min(j, total - 1);
        }
        blocks.push({ kind: "html", start: i + 1, end: end + 1, text: text });
        i = end + 1;
        continue;
      }
      if (isPipeLine(text)) {
        var s = i;
        while (i < total && (isPipeLine(lines[i].text || "") || isSepLine(lines[i].text || ""))) i++;
        var blockLines = [];
        for (var k = s; k < i; k++) blockLines.push(lines[k].text || "");
        var hasSep = blockLines.some(isSepLine);
        if (hasSep && blockLines.length >= 2 && blockLines.length <= 300) {
          blocks.push({ kind: "pipe", start: s + 1, end: i, lines: blockLines });
        }
        continue;
      }
      i++;
    }
    return blocks;
  }

  function tableBlockAt(line) {
    var blocks = state.tableBlocks || [];
    for (var i = 0; i < blocks.length; i++) {
      if (line >= blocks[i].start && line <= blocks[i].end) return blocks[i];
    }
    return null;
  }

  // 表格里有 rowspan/colspan 时，鼠标下看到的「这一行」和事件 target 的 tr 可能不是同一行
  // （一个跨 3 行的 td 属于第一行的 tr）。按坐标取真正落在光标下的那行，保证右键/点击
  // 作用在用户看到的那一行。
  function tableRowAtPoint(blockEl, x, y) {
    if (!blockEl) return null;
    var rows = blockEl._tableRows || blockEl.querySelectorAll("tbody tr");
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i].getBoundingClientRect();
      if (!r.height) continue;
      if (y >= r.top - 0.5 && y <= r.bottom + 0.5) return rows[i];
    }
    return null;
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
  // 表块 grid 体积大（山东自由裁量标准约 400KB），按文件懒加载；到货后只重画右栏卡片
  function loadTablesFor(doc, seq) {
    state.tables = null;
    getJSON("/api/tables?key=" + encodeURIComponent(doc.key)).then(function (payload) {
      if (seq !== loadDoc._seq || !state.doc || state.doc.key !== doc.key) return;
      if (!payload || !payload.tables || !Object.keys(payload.tables).length) return;
      state.tables = payload.tables;
      var chunksTop = el.chunks.scrollTop;
      var activeId = state.activeChunk;
      renderChunks(state.doc);
      el.chunks.scrollTop = chunksTop;
      if (activeId) {
        var chunk = findChunk(activeId);
        if (chunk) selectChunk(chunk, { scrollSource: false, scrollCard: false });
      }
    }).catch(function () {
      /* 表格视图加载失败不影响文字视图 */
    });
  }

  function loadDoc(file) {
    hideSplitCaret();
    var seq = (loadDoc._seq = (loadDoc._seq || 0) + 1);
    state.tables = null;
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
      loadTablesFor(doc, seq);
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
    state.tableBlocks = [];
    state.tableRows = {};
    state.hlRows = [];
    state.rowActive = [];
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

    // 表格块（管道表 / HTML 表）：在起始行渲染成真表格；后续行隐藏但保留 data-line 供映射
    var blocks = detectSourceTableBlocks(doc.lines);
    var blockByStart = {};
    var hiddenLine = {};
    blocks.forEach(function (block) {
      blockByStart[block.start] = block;
      for (var h = block.start + 1; h <= block.end; h++) hiddenLine[h] = true;
    });

    function barsForRange(start, end) {
      var bars = {};
      doc.chunks.forEach(function (chunk, idx) {
        if (!chunk.span) return;
        if (chunk.span[1] < start || chunk.span[0] > end) return;
        var lane = laneOfChunk[idx];
        var bar = bars[lane];
        if (!bar) bar = bars[lane] = { lane: lane, chunks: [], start: false, end: false };
        bar.chunks.push(idx);
        if (chunk.span[0] >= start && chunk.span[0] <= end) bar.start = true;
        if (chunk.span[1] >= start && chunk.span[1] <= end) bar.end = true;
        // 覆盖整块 → 是块内部的一段，画成连续竖条（不留断口）
        if (chunk.span[0] <= start && chunk.span[1] >= end) { bar.start = false; bar.end = false; }
      });
      return bars;
    }

    // 表格块的宿主源行：整张表在 .md 里往往只占一行（如山东自由裁量 23 万字符的 HTML 表），
    // 但切块器会把它切成成百上千个表格块，它们的 span 全是同一行 → 泳道会画成很多条，
    // 视觉上误导。超过泳道上限时合并成一根「聚合条」并在 meta 里标出切片数。
    function chunksInTableBlock(start, end) {
      var out = [];
      doc.chunks.forEach(function (chunk, idx) {
        if (!chunk.span) return;
        if (chunk.span[1] < start || chunk.span[0] > end) return;
        out.push(idx);
      });
      return out;
    }

    // 表格切片的 law_num 里带「表1 第X-Y行」：把渲染出来的表格行按所属切片着色，
    // 点行即选中该切片；悬停行只联动对应卡片（否则整块几百张卡片一起 related 会很卡）。
    var TABLE_ROW_RE = /第\s*(\d+)\s*[-–—~～]\s*(\d+)\s*行/;

    function tableRowRange(chunk) {
      if (chunk.chunk_kind !== "table" && chunk.chunk_kind !== "appendix") return null;
      var m = TABLE_ROW_RE.exec(chunk.law_num || "");
      if (!m) return null;
      var a = parseInt(m[1], 10);
      var b = parseInt(m[2], 10);
      if (!isFinite(a) || !isFinite(b) || a < 1 || b < a) return null;
      return { start: a, end: b };
    }

    function rowHue(idx) {
      return Math.round((idx * 137.508) % 360);
    }

    // 把 blockChunks（chunk 下标数组）对应的行区间映射到渲染出的 tr 上，返回映射成功的行数
    function annotateTableRows(blockChunks, txtEl) {
      var table = txtEl.querySelector("table");
      if (!table) return 0;
      var bodyRows = [];
      if (table.tBodies && table.tBodies.length) {
        for (var t = 0; t < table.tBodies.length; t++) {
          var rows = table.tBodies[t].rows;
          for (var ri = 0; ri < rows.length; ri++) bodyRows.push(rows[ri]);
        }
      } else {
        for (var rj = 0; rj < table.rows.length; rj++) bodyRows.push(table.rows[rj]);
      }
      if (!bodyRows.length) return 0;
      // 行号 → tr 的偏移：表头在 thead 时不偏移；表头混在 tbody 里时跳过第一行。
      // 源 HTML 表常把表头写成 <tr><td>…</td></tr>（无 th），用「首行无合并单元格、后面行有」
      // 和「行号上界 == tbody 行数」两个信号判断首行是不是表头。
      var offset = 0;
      if (!table.tHead) {
        var first = bodyRows[0];
        var headerHint = !!(first && first.cells.length && first.cells[0].tagName === "TH");
        if (!headerHint && bodyRows.length > 1) {
          var firstHasSpan = Array.prototype.some.call(first.cells, function (c) {
            return c.rowSpan > 1 || c.colSpan > 1;
          });
          var laterHasSpan = false;
          for (var bri = 1; bri < bodyRows.length && !laterHasSpan; bri++) {
            laterHasSpan = Array.prototype.some.call(bodyRows[bri].cells, function (c) {
              return c.rowSpan > 1 || c.colSpan > 1;
            });
          }
          if (!firstHasSpan && laterHasSpan) headerHint = true;
        }
        var maxEnd = 0;
        blockChunks.forEach(function (idx) {
          var rr = tableRowRange(doc.chunks[idx]);
          if (rr && rr.end > maxEnd) maxEnd = rr.end;
        });
        if (maxEnd === bodyRows.length) headerHint = false;
        if (maxEnd > 0 && maxEnd <= bodyRows.length - 1) headerHint = true;
        offset = headerHint ? 1 : 0;
      }
      var mapped = 0;
      blockChunks.forEach(function (idx) {
        var chunk = doc.chunks[idx];
        var range = tableRowRange(chunk);
        if (!range) return;
        var hue = rowHue(idx);
        for (var n = range.start; n <= range.end; n++) {
          var tr = bodyRows[n - 1 + offset];
          if (!tr) continue;
          tr.classList.add("chunk-row");
          tr.dataset.chunk = String(idx);
          tr.dataset.rowno = String(n); // 表内行号（law_num 里的「第X行」），用于新建切片的条号推算
          tr.style.setProperty("--row-hue", String(hue));
          tr.title = "切片 #" + (idx + 1) + " · " + chunk.id + " · " + (chunk.law_num || "") +
            " · 点击选中该切片";
          if (!state.tableRows[chunk.id]) state.tableRows[chunk.id] = [];
          state.tableRows[chunk.id].push(tr);
          mapped++;
        }
      });
      // 渲染出来的表格行里，没有被任何表格切片覆盖的行 → 标成「未切片」行，
      // 让用户一眼看出哪里是缺口，并且可以拖选后右键新增切片。
      bodyRows.forEach(function (tr) {
        if (tr.dataset.chunk === undefined) {
          tr.classList.add("row-gap");
          tr.title = "这一行没有切片覆盖（可拖选 / 右键用这些行新增切片）";
        }
      });
      return mapped;
    }

    function appendLanes(lanesEl, bars, lineNo) {
      Object.keys(bars).forEach(function (key) {
        var cell = bars[key];
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
          bar.title = "第 " + lineNo + " 行被 " + cell.chunks.length + " 个切片覆盖：" + ids +
            (cell.chunks.length > 4 ? " …" : "");
        }
        lanesEl.appendChild(bar);
      });
    }

    for (var j = 0; j < total; j++) {
      var line = doc.lines[j];
      var block = blockByStart[line.n];
      var row = document.createElement("div");
      var no = document.createElement("span");
      no.className = "no";
      var lanes = document.createElement("span");
      lanes.className = "lanes";
      var txt = document.createElement("span");
      txt.className = "txt";

      if (block) {
        row.className = "line kind-table table-block";
        row.dataset.line = String(line.n);
        row.dataset.lineEnd = String(block.end);
        no.textContent = block.start === block.end
          ? String(block.start)
          : block.start + "–" + block.end;

        var blockChunks = chunksInTableBlock(block.start, block.end);
        var blockRange = block.start === block.end
          ? String(block.start)
          : block.start + "–" + block.end;
        var blockMeta = (block.kind === "pipe" ? "管道表" : "HTML 表") + " · 第 " + blockRange + " 行";
        var blockSuffix = blockChunks.length > 1 ? " · " + blockChunks.length + " 个切片" : "";
        if (blockChunks.length > MAX_LANES) {
          // 一张表被切成了很多块、span 又都是同一源码行：合并成一根聚合条，避免误导成「四条不同的块」
          var agg = document.createElement("span");
          agg.className = "lane lane-agg";
          agg.dataset.chunk = String(blockChunks[0]);
          var firstChunk = doc.chunks[blockChunks[0]];
          agg.title = "表格块 · 第 " + blockRange + " 行 · " + blockChunks.length +
            " 个表格切片叠加在这一行（源文件里整张表只占一行）· 点击选中第一个：" + firstChunk.id;
          lanes.appendChild(agg);
        } else {
          appendLanes(lanes, barsForRange(block.start, block.end), block.start);
        }

        var tableHtml = null;
        if (block.kind === "pipe") {
          tableHtml = buildPipeTableHtml(block.lines,
            blockMeta + " · 源文件原样解析" + blockSuffix);
        } else {
          tableHtml = buildHtmlTablesHtml(block.text,
            blockMeta + " · 源文件原样渲染" + blockSuffix);
        }
        if (tableHtml) {
          txt.innerHTML = tableHtml;
          row.className += " table-rendered";
          annotateTableRows(blockChunks, txt);
          row._tableRows = txt.querySelectorAll("tbody tr");
        } else {
          txt.textContent = line.text || "";
        }
        state.tableBlocks.push({ start: block.start, end: block.end, el: row });
      } else if (hiddenLine[line.n]) {
        row.className = "line table-cont";
        row.dataset.line = String(line.n);
      } else {
        row.className = "line kind-" + line.kind;
        row.dataset.line = String(line.n);
        no.textContent = String(line.n);
        appendLanes(lanes, laneBuckets[line.n] || {}, line.n);
        if (line.text === "") {
          row.className += " blank";
        } else {
          txt.textContent = line.text;
        }
        if (line.truncated) row.className += " truncated";
      }

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
      // 合并/大块：从原文行倒推这块实际覆盖的条号范围（视图层提示，不改 15 字段契约）
      var cover = coveredLawNums(chunk);
      if (cover.length >= 2) {
        badges.push("<span class='badge badge-cover' title='覆盖：" + escapeHtml(cover.join(" / ")) + "'>覆盖 " +
          escapeHtml(cover[0]) + "–" + escapeHtml(cover[cover.length - 1]) + "（" + cover.length + " 条）</span>");
      }
      if (chunk.part_total > 1) {
        badges.push("<span class='badge badge-part'>part " + chunk.part_index + "/" + chunk.part_total + "</span>");
      }
      badges.push("<span class='badge badge-align " + escapeHtml(chunk.align) + "'>" +
        escapeHtml(ALIGN_LABEL[chunk.align] || chunk.align) + "</span>");
      if (chunk.manual) badges.push("<span class='badge badge-manual'>手工</span>");
      head.innerHTML = "<span class='chunk-seq'>#" + (index + 1) + "</span>" + badges.join("");
      card.appendChild(head);

      var body = document.createElement("div");
      body.className = "chunk-content";
      body.innerHTML = chunkBodyHtml(chunk);
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
    // 表格块：块内部行已被隐藏，把高亮落到整张表格上，保证区块可见
    (state.tableBlocks || []).forEach(function (block) {
      for (var b = block.start; b <= block.end; b++) {
        var lineRow = state.lineEls[b - 1];
        if (lineRow && lineRow.classList.contains(cls)) {
          block.el.classList.add(cls);
          state.hlEls.push(block.el);
          return;
        }
      }
    });
    // 表格内部行级高亮：只高亮该切片对应的行（点表格行/悬停卡片时最直观）
    var tRows = (state.tableRows && state.tableRows[chunk.id]) || [];
    var rowCls = strong ? "row-hl" : "row-hl-soft";
    tRows.forEach(function (tr) {
      tr.classList.add(rowCls);
      state.hlRows.push(tr);
    });
  }

  function clearLineHighlight() {
    for (var i = 0; i < state.hlEls.length; i++) {
      state.hlEls[i].classList.remove("hl", "hl-soft");
    }
    state.hlEls = [];
    for (var k = 0; k < state.hlRows.length; k++) {
      state.hlRows[k].classList.remove("row-hl", "row-hl-soft");
    }
    state.hlRows = [];
  }

  function highlightRelatedCards(line, endLine) {
    var doc = state.doc;
    if (!doc) return [];
    if (state.cardEls._related) {
      state.cardEls._related.forEach(function (id) {
        if (state.cardEls[id]) state.cardEls[id].classList.remove("related");
      });
    }
    var last = endLine || line;
    var related = [];
    var seen = {};
    for (var n = line; n <= last; n++) {
      var arr = (state.lineChunks && state.lineChunks[n]) || [];
      arr.forEach(function (chunk) {
        if (seen[chunk.id]) return;
        seen[chunk.id] = 1;
        related.push(chunk);
      });
    }
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

  // 选区落在渲染出来的表格行上 → { block, from, to, rows, text, allGap }
  // 表格行没有源行级坐标，所以 span 用整个表格块的行区间；正文用选中行的单元格文本。
  function selectedTableRows() {
    var sel = window.getSelection ? window.getSelection() : null;
    if (!sel || sel.isCollapsed || !sel.rangeCount || !state.doc) return null;
    var range = sel.getRangeAt(0);
    if (!range || !el.source.contains(range.startContainer) || !el.source.contains(range.endContainer)) return null;
    var startEl = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
    var endEl = range.endContainer.nodeType === 1 ? range.endContainer : range.endContainer.parentElement;
    var blockEl = startEl && startEl.closest ? startEl.closest(".line.table-block") : null;
    if (!blockEl) return null;
    var endBlock = endEl && endEl.closest ? endEl.closest(".line.table-block") : null;
    if (endBlock !== blockEl) return null;
    var block = tableBlockAt(parseInt(blockEl.dataset.line, 10));
    if (!block) return null;
    var all = blockEl.querySelectorAll("tbody tr");
    var picked = [];
    for (var i = 0; i < all.length; i++) {
      if (range.intersectsNode(all[i])) picked.push({ tr: all[i], index: i + 1 });
    }
    if (!picked.length) return null;
    // 保留空单元格（首/尾列可能为空）——用 \t 占位，手工块右栏才能按原列数还原网格
    var text = picked.map(function (item) {
      var cells = [];
      for (var c = 0; c < item.tr.cells.length; c++) cells.push(String(item.tr.cells[c].innerText || "").trim());
      return cells.join("\t");
    }).join("\n");
    var allGap = picked.every(function (item) { return item.tr.dataset.chunk === undefined; });
    return {
      block: block,
      blockEl: blockEl,
      from: picked[0].index,
      to: picked[picked.length - 1].index,
      rows: picked.map(function (item) { return item.tr; }),
      text: text,
      allGap: allGap
    };
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
    // 表格行选区：正文取选中行的单元格文本，行区间取整个表格块（源行粒度到不了单元格）
    var tbl = selectedTableRows();
    if (tbl) {
      var tblText = tbl.text;
      if (tblText.length > 20000) tblText = tblText.slice(0, 20000) + "…";
      return {
        start: tbl.block.start,
        end: tbl.block.end,
        text: tblText,
        tableRows: [tbl.from, tbl.to],
        tableRowsArr: tbl.rows,
        tableGap: tbl.allGap
      };
    }
    if (p1.line > p2.line) { var tmp = p1; p1 = p2; p2 = tmp; }
    var a = p1.line;
    var b = p2.line;
    // 表格块内部选文字：表格是渲染出来的，字符偏移对不上源行 → 直接按整块行区间取源行原文
    var blockA = tableBlockAt(a);
    var blockB = tableBlockAt(b);
    if (blockA || blockB) {
      var startLine = blockA ? Math.min(blockA.start, a) : a;
      var endLine = blockB ? Math.max(blockB.end, b) : b;
      var srcLines = state.doc.lines || [];
      var blockParts = [];
      for (var q = startLine; q <= endLine; q++) {
        blockParts.push(srcLines[q - 1] ? (srcLines[q - 1].text || "") : "");
      }
      var blockText = blockParts.join("\n");
      if (blockText.length > 20000) blockText = blockText.slice(0, 20000) + "…";
      return { start: startLine, end: endLine, text: blockText };
    }
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

  // ---------- 光标/选区位置 → 切片内拆分点 ----------
  function unescapeHtml(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&nbsp;/g, " ")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&amp;/g, "&");
  }

  // 与后端 align.norm_line 同口径：去图片/HTML/面包屑/加粗/LaTeX 转义/全部空白
  function normJs(raw) {
    var s = String(raw === null || raw === undefined ? "" : raw);
    s = s.replace(/!\[[^\]]*\]\([^)]*\)/g, "[图示]").replace(/<img[^>]*>/gi, "[图示]");
    s = s.replace(/<[^>]+>/g, " ");
    s = unescapeHtml(s);
    if (s.indexOf(" > ") >= 0) s = s.slice(s.lastIndexOf(" > ") + 3);
    s = s.replace(/\*\*/g, "");
    s = s.replace(/\\\(/g, "（").replace(/\\\)/g, "）").replace(/\\\[/g, "【").replace(/\\\]/g, "】");
    return s.replace(/\s+/g, "");
  }

  // 源行字符偏移 → 清洗后正文行字符偏移（跳过被删掉的标记/空白/面包屑）
  function mapCharOffset(raw, cleaned, offset) {
    var r = String(raw || "");
    var c = String(cleaned || "");
    if (offset <= 0) return 0;
    var ci = 0;
    var limit = Math.min(offset, r.length);
    for (var i = 0; i < limit; i++) {
      var found = c.indexOf(r.charAt(i), ci);
      if (found >= 0) ci = found + 1;
    }
    return ci;
  }

  // 屏幕坐标/选区 → 源文位置 { line, offset }
  function sourcePosition(ev) {
    var sel = window.getSelection ? window.getSelection() : null;
    if (sel && !sel.isCollapsed && sel.rangeCount) {
      var range = sel.getRangeAt(0);
      if (range && el.source.contains(range.startContainer) && el.source.contains(range.endContainer)) {
        var line = lineOfNode(range.startContainer);
        if (line) return { line: line, offset: lineCharOffset(state.lineEls[line - 1], range.startContainer, range.startOffset) };
      }
    }
    var caret = null;
    if (document.caretRangeFromPoint) caret = document.caretRangeFromPoint(ev.clientX, ev.clientY);
    if (caret && el.source.contains(caret.startContainer)) {
      var line2 = lineOfNode(caret.startContainer);
      if (line2) return { line: line2, offset: lineCharOffset(state.lineEls[line2 - 1], caret.startContainer, caret.startOffset) };
    }
    // 兜底：右键所在行 → 行首
    var row = ev.target && ev.target.closest ? ev.target.closest(".line") : null;
    if (row && row.dataset.line) return { line: parseInt(row.dataset.line, 10), offset: 0 };
    return null;
  }

  // 把源文位置换算成 split 命令：优先行边界，否则字符级
  function splitPlanAt(chunk, pos) {
    if (!chunk || !chunk.span || !pos || !state.doc) return null;
    if (pos.line < chunk.span[0] || pos.line > chunk.span[1]) return null;
    var srcLine = state.doc.lines[pos.line - 1];
    if (!srcLine || srcLine.truncated) return null; // 折叠超长行无法精确映射
    var chunkLines = String(chunk.chunk || "").split("\n");
    if (!chunkLines.length) return null;
    var target = normJs(srcLine.text || "");
    var j = -1;
    for (var i = 0; i < chunkLines.length; i++) {
      if (target && normJs(chunkLines[i]) === target) { j = i; break; }
    }
    if (j < 0 && target) {
      for (var k = 0; k < chunkLines.length; k++) {
        var n = normJs(chunkLines[k]);
        if (n && (n.indexOf(target) >= 0 || target.indexOf(n) >= 0)) { j = k; break; }
      }
    }
    if (j < 0) {
      var spanLen = chunk.span[1] - chunk.span[0] + 1;
      j = Math.round((pos.line - chunk.span[0]) / Math.max(1, spanLen) * chunkLines.length);
      j = Math.max(0, Math.min(chunkLines.length - 1, j));
    }
    if (j === 0 && pos.offset === 0) return null; // 块首无法拆分
    if (j > 0 && pos.offset === 0) return { kind: "line", at_line: j, textLine: j };
    var text = String(chunk.chunk || "");
    var prefix = 0;
    for (var m = 0; m < j; m++) prefix += chunkLines[m].length + 1;
    var lineClean = chunkLines[j] || "";
    var content = lineClean;
    if (lineClean.indexOf(" > ") >= 0) content = lineClean.slice(lineClean.lastIndexOf(" > ") + 3);
    var charInLine = mapCharOffset(srcLine.text || "", content, pos.offset);
    var atChar = prefix + (lineClean.length - content.length) + charInLine;
    if (atChar <= 0 || atChar >= text.length) return null; // 块尾无法拆分
    return { kind: "char", at_char: atChar, textLine: j };
  }

  function splitAtCursor(chunk, pos) {
    var plan = splitPlanAt(chunk, pos);
    if (!plan) {
      showToast("光标位置无法定位拆分点（可能落在块首/块尾、或该行已折叠）", "warn");
      return;
    }
    var op = { op: "split", id: chunk.id };
    if (plan.kind === "line") op.at_line = plan.at_line;
    else op.at_char = plan.at_char;
    var index = state.doc ? state.doc.chunks.indexOf(chunk) : -1;
    postOp(op, (plan.kind === "line" ? "已在第 " + plan.at_line + " 行处拆分 " : "已在光标处拆分 ") + chunk.id + "（Ctrl+Z 可撤销）",
      function (doc) {
        // 拆完选中「下半块」并滚到它，让用户立刻看到结果
        var next = doc && doc.chunks ? doc.chunks[index + 1] : null;
        if (next) selectChunk(next, { scrollSource: false, scrollCard: true });
      });
  }

  // ---------- 光标反馈：点击/右键时画出蓝色拆分光标 + 位置提示 ----------
  function clearTargetCard() {
    if (state.targetChunk && state.cardEls[state.targetChunk]) {
      state.cardEls[state.targetChunk].classList.remove("target");
    }
    state.targetChunk = null;
  }

  function hideSplitCaret() {
    if (el.splitCaret) el.splitCaret.hidden = true;
    if (el.splitHint) el.splitHint.hidden = true;
    clearTargetCard();
  }

  function showSplitCaret(ev) {
    if (!state.doc) { hideSplitCaret(); return; }
    var pos = sourcePosition(ev);
    if (!pos) { hideSplitCaret(); return; }
    var rect = null;
    try {
      var caret = document.caretRangeFromPoint
        ? document.caretRangeFromPoint(ev.clientX, ev.clientY)
        : (document.caretPositionFromPoint ? document.caretPositionFromPoint(ev.clientX, ev.clientY) : null);
      if (caret && caret.cloneRange) {
        var r = caret.cloneRange();
        if (r.getClientRects().length) rect = r.getBoundingClientRect();
      } else if (caret) {
        var r2 = document.createRange();
        r2.setStart(caret.offsetNode, caret.offset);
        r2.setEnd(caret.offsetNode, caret.offset);
        if (r2.getClientRects().length) rect = r2.getBoundingClientRect();
      }
    } catch (e) { rect = null; }
    if (!rect || (rect.width === 0 && rect.height === 0)) {
      var rowEl = state.lineEls[pos.line - 1];
      if (rowEl) {
        var rr = rowEl.getBoundingClientRect();
        rect = { left: rr.left + 58, top: rr.top, height: rr.height };
      }
    }
    if (!rect) { hideSplitCaret(); return; }
    var chunk = chunkAtLine(pos.line);
    var block = tableBlockAt(pos.line);
    var plan = (chunk && !block) ? splitPlanAt(chunk, pos) : null;
    el.splitCaret.hidden = false;
    el.splitCaret.style.left = Math.round(rect.left - 1) + "px";
    el.splitCaret.style.top = Math.round(rect.top) + "px";
    el.splitCaret.style.height = Math.max(14, Math.round(rect.height)) + "px";
    var hint;
    if (block) {
      hint = "▦ 表格块 · 第 " + block.start + "–" + block.end + " 行 · 右键可编辑此切片正文 / 拆分整块" +
        (chunk ? " · " + chunk.id : "");
    } else if (chunk && plan) {
      hint = "✂ 第 " + pos.line + " 行 · " + (plan.kind === "line" ? "行边界拆分" : "光标处拆分") + " · " + chunk.id;
    } else if (chunk) {
      hint = "✂ 第 " + pos.line + " 行 · 此位置不可拆分（块首/块尾/折叠行），可点行内文字或拖选";
    } else {
      hint = "第 " + pos.line + " 行 · 此处不属于任何切片";
    }
    el.splitHint.textContent = hint;
    el.splitHint.hidden = false;
    var hw = el.splitHint.offsetWidth || 240;
    var hx = Math.min(Math.max(8, rect.left + 6), (window.innerWidth || 1280) - hw - 8);
    var hy = Math.max(8, rect.top - (el.splitHint.offsetHeight || 22) - 6);
    el.splitHint.style.left = Math.round(hx) + "px";
    el.splitHint.style.top = Math.round(hy) + "px";
    clearTargetCard();
    if (chunk && state.cardEls[chunk.id]) {
      state.cardEls[chunk.id].classList.add("target");
      state.targetChunk = chunk.id;
    }
  }

  function clearSelectionMarks() {
    (state.selHits || []).forEach(function (id) {
      if (state.cardEls[id]) state.cardEls[id].classList.remove("hit");
    });
    (state.selTableRows || []).forEach(function (tr) {
      tr.classList.remove("sel-row");
    });
    state.selTableRows = [];
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
    // 表格行选区（尤其是未切片行）：按表格行 mark，并提示可以右键新增切片
    var tbl = selectedTableRows();
    if (tbl) {
      clearSelectionMarks();
      state.selTableRows = tbl.rows;
      tbl.rows.forEach(function (tr) { tr.classList.add("sel-row"); });
      var label = tbl.from === tbl.to
        ? ("表格内第 " + tbl.from + " 行")
        : ("表格内第 " + tbl.from + "–" + tbl.to + " 行");
      if (tbl.allGap) {
        setStatus(label + " · 未切片（" + tbl.rows.length + " 行）· 右键可「用这些行新增切片」", "warn");
      } else {
        setStatus(label + " · 已选 " + tbl.rows.length + " 行 · 右键可新增切片或选中对应切片", "ok");
      }
      return;
    }
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
    if (!anyVisible && el.ctxMenu.hidden) scrollToCard(hits[0].id);
  }

  function scheduleSelectionCheck() {
    window.clearTimeout(scheduleSelectionCheck._t);
    scheduleSelectionCheck._t = window.setTimeout(applySelectionNow, 120);
  }

  // 最近 200ms 内是否有程序化滚动（避免选区联动把刚弹出的右键菜单关掉）
  function programmaticScrollNow() {
    return !!(state.programmaticScroll && Date.now() - state.programmaticScroll < 200);
  }

  function scrollToLine(n) {
    var row = state.lineEls[n - 1];
    if (!row) return;
    var container = el.source;
    var top = row.offsetTop - container.clientHeight / 2 + row.offsetHeight / 2;
    state.programmaticScroll = Date.now();
    container.scrollTop = Math.max(0, top);
  }

  function scrollToCard(chunkId) {
    var card = state.cardEls[chunkId];
    if (!card) return;
    var container = el.chunks;
    var top = card.offsetTop - container.clientHeight / 2 + card.offsetHeight / 2;
    state.programmaticScroll = Date.now();
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
    // 表格内部行：选中对应的行，并在表格容器内滚到可见
    state.rowActive.forEach(function (tr) { tr.classList.remove("row-active"); });
    state.rowActive = [];
    var activeRows = (state.tableRows && state.tableRows[chunk.id]) || [];
    activeRows.forEach(function (tr) {
      tr.classList.add("row-active");
      state.rowActive.push(tr);
    });
    if (activeRows.length) {
      var wrap = activeRows[0].closest(".tblwrap");
      if (wrap) wrap.scrollTop = Math.max(0, activeRows[0].offsetTop - wrap.clientHeight / 2);
    }
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
      // 用坐标取「看得见的那一行」，避免 rowspan 单元格把鼠标事件挂到上一行
      var trow = tableRowAtPoint(ev.target.closest(".line.table-block"), ev.clientX, ev.clientY);
      if (trow && trow.dataset.chunk !== undefined) {
        var tidx = parseInt(trow.dataset.chunk, 10);
        var tchunk = state.doc && state.doc.chunks[tidx];
        if (tchunk) {
          state.hoverLine = null;
          clearRelatedCards();
          if (state.cardEls[tchunk.id]) state.cardEls[tchunk.id].classList.add("related");
          state.cardEls._related = [tchunk.id];
          return;
        }
      }
      var row = ev.target.closest(".line");
      if (!row) return;
      var line = parseInt(row.dataset.line, 10);
      if (state.hoverLine === line) return;
      state.hoverLine = line;
      var end = row.dataset.lineEnd ? parseInt(row.dataset.lineEnd, 10) : line;
      highlightRelatedCards(line, end);
    });
    el.source.addEventListener("mouseout", function (ev) {
      var trow = tableRowAtPoint(ev.target.closest(".line.table-block"), ev.clientX, ev.clientY);
      if (trow) {
        if (ev.relatedTarget && trow.contains(ev.relatedTarget)) return;
        clearRelatedCards();
        return;
      }
      var row = ev.target.closest(".line");
      if (!row) return;
      if (ev.relatedTarget && el.source.contains(ev.relatedTarget) && ev.relatedTarget.closest(".line")) {
        var nextRow = ev.relatedTarget.closest(".line");
        if (row === nextRow) return;
      }
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
      var tblBlockEl = ev.target.closest(".line.table-block");
      var tblRow = tableRowAtPoint(tblBlockEl, ev.clientX, ev.clientY);
      if (tblRow && tblRow.dataset.chunk === undefined) {
        // 未切片的表格行：把这行内容设为选区，交给 applySelectionNow 统一标记与提示
        var gSel = window.getSelection ? window.getSelection() : null;
        if (gSel) {
          var gRange = document.createRange();
          gRange.selectNodeContents(tblRow);
          gSel.removeAllRanges();
          gSel.addRange(gRange);
        }
        scheduleSelectionCheck();
        return;
      }
      if (tblRow) {
        var tidx = parseInt(tblRow.dataset.chunk, 10);
        var tchunk = state.doc && state.doc.chunks[tidx];
        if (tchunk) {
          selectChunk(tchunk, { scrollSource: false, scrollCard: true });
          return;
        }
      }
      var row = ev.target.closest(".line");
      if (!row) return;
      var line = parseInt(row.dataset.line, 10);
      var end = row.dataset.lineEnd ? parseInt(row.dataset.lineEnd, 10) : line;
      var related = highlightRelatedCards(line, end);
      if (related && related.length) {
        selectChunk(related[0], { scrollSource: false, scrollCard: true });
      }
    });
    // 拖选原文文字：选区覆盖的行 → 高亮对应切片
    el.source.addEventListener("mouseup", function () {
      window.setTimeout(applySelectionNow, 0);
    });
    document.addEventListener("selectionchange", scheduleSelectionCheck);
    // 左键/右键按下时给出可见的拆分光标反馈（蓝色竖线 + 上方提示）
    el.source.addEventListener("mousedown", function (ev) {
      if (ev.button !== 0 && ev.button !== 2) return;
      if (ev.target.closest(".lane")) return;
      if (ev.target.closest(".no")) { hideSplitCaret(); return; }
      showSplitCaret(ev);
    });
    // 右键：在原文选区/切片上弹出上下文工作栏
    el.source.addEventListener("contextmenu", function (ev) {
      showSplitCaret(ev);
      // 右键以菜单为主，隐藏上方气泡提示，避免「不可拆分」等文案干扰
      el.splitHint.hidden = true;
      openContextMenu(ev);
    });
  }

  // ---------- 右键上下文菜单：原文选区 ↔ 切片操作 ----------
  function placeCtxMenu(ev) {
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

  function chunkAtLine(line) {
    var doc = state.doc;
    if (!doc || !line) return null;
    var best = null;
    var bestLen = Infinity;
    for (var i = 0; i < doc.chunks.length; i++) {
      var c = doc.chunks[i];
      if (!c.span || c.span[0] > line || line > c.span[1]) continue;
      var len = c.span[1] - c.span[0];
      if (len < bestLen) { best = c; bestLen = len; }
    }
    return best;
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
      if (typeof action === "function") action();
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
    var pos = sourcePosition(ev);
    var line = row && row.dataset.line ? parseInt(row.dataset.line, 10) : 0;
    if (!line && picked) line = picked.start;
    if (!line && pos) line = pos.line;
    // 表格行：按鼠标坐标取「看得见的那一行」（rowspan 会让事件 target 落在上一行的 tr 里），
    // 再读行上记录的块下标。整张表在源文件里可能只占一行（山东 679 块都在第 588 行），
    // chunkAtLine 只能取到列表里最靠前的块，不能用于表格行。
    var tblBlockForMenu = ev.target && ev.target.closest ? ev.target.closest(".line.table-block") : null;
    var rowTr = tableRowAtPoint(tblBlockForMenu, ev.clientX, ev.clientY);
    var rowChunk = null;
    if (rowTr && rowTr.dataset.chunk !== undefined) {
      rowChunk = state.doc.chunks[parseInt(rowTr.dataset.chunk, 10)] || null;
    }
    var target = rowChunk || chunkAtLine(line);
    if (!target && picked) {
      var hits = chunksInLineRange(picked.start, picked.end);
      target = hits.length ? hits[0] : null;
    }
    // 表格行不是源码里的连续文字，没有「光标处拆分」可言
    var plan = (target && !rowTr) ? splitPlanAt(target, pos || { line: line, offset: 0 }) : null;

    el.ctxMenu.innerHTML = "";
    var count = 0;
    var doc = state.doc;

    // 表格行选区 / 右键落在未切片表格行 → 专用菜单：用这些行新增切片
    var tblSel = selectedTableRows();
    var gapTrInfo = null;
    if (!tblSel) {
      var gapTr = ev.target && ev.target.closest ? ev.target.closest(".line.table-block tbody tr") : null;
      if (gapTr && gapTr.dataset.chunk === undefined) {
        var gapBlockEl = gapTr.closest(".line.table-block");
        var gapBlock = gapBlockEl ? tableBlockAt(parseInt(gapBlockEl.dataset.line, 10)) : null;
        if (gapBlock) {
          var allGapRows = gapBlockEl.querySelectorAll("tbody tr");
          var gapIdx = 1;
          for (var gi = 0; gi < allGapRows.length; gi++) {
            if (allGapRows[gi] === gapTr) { gapIdx = gi + 1; break; }
          }
          var gapCells = [];
          for (var gc = 0; gc < gapTr.cells.length; gc++) gapCells.push(String(gapTr.cells[gc].innerText || "").trim());
          gapTrInfo = { block: gapBlock, from: gapIdx, to: gapIdx, rows: [gapTr], text: gapCells.join("\t"), allGap: true };
        }
      }
    }
    var rowSel = tblSel || gapTrInfo;
    if (rowSel) {
      var rowLabel = rowSel.from === rowSel.to
        ? ("表格内第 " + rowSel.from + " 行")
        : ("表格内第 " + rowSel.from + "–" + rowSel.to + " 行");
      el.ctxMenu.appendChild(ctxItem(
        rowSel.allGap ? ("ℹ " + rowLabel + " 没有切片覆盖") : ("ℹ " + rowLabel + " · 已选 " + rowSel.rows.length + " 行"),
        rowSel.allGap ? "未切片区域 · 可新增切片" : "表格行选区",
        null,
        true
      ));
      var gapText = rowSel.text;
      if (gapText.length > 20000) gapText = gapText.slice(0, 20000) + "…";
      el.ctxMenu.appendChild(ctxItem(
        rowSel.from === rowSel.to ? "✚ 用这一行新增切片" : "✚ 用选中的表格行新增切片",
        rowLabel + " · " + gapText.length + " 字",
        (function (info, txt) {
          return function () {
            openAdd({
              start: info.block.start,
              end: info.block.end,
              text: txt,
              tableRows: [info.from, info.to],
              tableRowsArr: info.rows,
              tableGap: info.allGap
            });
          };
        })(rowSel, gapText)
      ));
      placeCtxMenu(ev);
      return;
    }

    if (plan) {
      el.ctxMenu.appendChild(ctxItem(
        plan.kind === "line" ? "✂ 在此处拆分（行边界）" : "✂ 在光标处拆分",
        "第 " + line + " 行 · " + target.id,
        function () { splitAtCursor(target, pos || { line: line, offset: 0 }); }
      ));
      count++;
    }

    if (picked && picked.text.trim()) {
      var rangeLabel = picked.start === picked.end
        ? ("第 " + picked.start + " 行")
        : ("第 " + picked.start + "–" + picked.end + " 行");
      el.ctxMenu.appendChild(ctxItem("✚ 用选区新增切片", rangeLabel + " · " + picked.text.length + " 字", function () {
        openAdd(picked);
      }));
      count++;
    }

    // 右键落在表格块上（没选中文字时）→ 直接给「在表格块处新增切片」
    var hereBlock = tableBlockAt(line);
    if (hereBlock && !(picked && picked.text.trim())) {
      var rawParts = [];
      var blockLines = doc.lines || [];
      for (var bq = hereBlock.start; bq <= hereBlock.end; bq++) {
        rawParts.push(blockLines[bq - 1] ? (blockLines[bq - 1].text || "") : "");
      }
      var rawText = rawParts.join("\n");
      if (rawText.length > 20000) rawText = rawText.slice(0, 20000) + "…";
      el.ctxMenu.appendChild(ctxItem(
        "✚ 在表格块处新增切片",
        "第 " + hereBlock.start + "–" + hereBlock.end + " 行",
        (function (b, t) {
          return function () {
            openAdd({ start: b.start, end: b.end, text: t, tableBlockRange: { start: b.start, end: b.end } });
          };
        })(hereBlock, rawText)
      ));
      count++;
    }

    // 选区覆盖了 2 个以上切片 → 集体合并（保留第一块的 id / 条号 / kind）
    if (picked && picked.start < picked.end) {
      var selHits = chunksInLineRange(picked.start, picked.end);
      if (selHits.length >= 2) {
        var firstIdx = doc.chunks.indexOf(selHits[0]);
        var lastIdx = doc.chunks.indexOf(selHits[selHits.length - 1]);
        var group = doc.chunks.slice(firstIdx, lastIdx + 1);
        if (group.length >= 2) {
          el.ctxMenu.appendChild(ctxItem(
            "⤵ 合并所选 " + group.length + " 个切片",
            "第 " + picked.start + "–" + picked.end + " 行",
            function () { mergeMany(group); }
          ));
          count++;
        }
      }
    }

    if (target) {
      var idx = doc.chunks.indexOf(target);
      if (count) el.ctxMenu.appendChild(ctxSep());
      var rangeHint = target.span
        ? ("原文第 " + target.span[0] + "–" + target.span[1] + " 行")
        : "未对齐";
      el.ctxMenu.appendChild(ctxItem("✎ 编辑此切片正文 / 条号…", target.id + " · " + rangeHint, function () { openEdit(target); }));
      el.ctxMenu.appendChild(ctxItem("✂ 拆分此切片…", target.id, function () { openSplit(target); }));
      el.ctxMenu.appendChild(ctxItem("⤴ 并入上一块", "", function () { mergeChunk(target, "prev"); }, idx <= 0));
      el.ctxMenu.appendChild(ctxItem("⤵ 并入下一块", "", function () { mergeChunk(target, "next"); }, idx >= doc.chunks.length - 1));
      el.ctxMenu.appendChild(ctxItem("✕ 删除此切片", target.id, function () { dropChunk(target); }));
      el.ctxMenu.appendChild(ctxSep());
      el.ctxMenu.appendChild(ctxItem("◎ 定位到切片", rangeHint, function () {
        selectChunk(target, { scrollSource: true, scrollCard: true });
      }));
      count++;
    } else if (line >= 1) {
      // 右键落在没有切片覆盖的原文行（标题/目录/空行/未对齐区域）：
      // 依然弹出菜单，给出说明与「用这一行新增切片」，避免用户以为右键坏了。
      var rowText = doc.lines && doc.lines[line - 1] ? String(doc.lines[line - 1].text || "") : "";
      if (count) el.ctxMenu.appendChild(ctxSep());
      el.ctxMenu.appendChild(ctxItem("ℹ 第 " + line + " 行不属于任何切片", "标题 / 目录 / 空行 / 未对齐区域", null, true));
      if (rowText.trim()) {
        el.ctxMenu.appendChild(ctxItem("✚ 用这一行新增切片", "第 " + line + " 行 · " + rowText.length + " 字",
          function () { openAdd({ start: line, end: line, text: rowText }); }));
      }
      count++;
    }

    if (!count) { hideCtxMenu(); return; }
    placeCtxMenu(ev);
  }

  // ---------- P2：人工编辑（拆 / 并 / 删 / 增 / 改 + 撤销重做） ----------
  var CHUNK_KINDS = ["clause", "table", "appendix", "paragraph", "stub", "front", "title"];

  // 条号识别（与后端 ops.detect_law_num 同口径）：
  // - 法规式「第X条」；- 标准式「5.4.1」（至少一个层级点，避免误吞正文里的普通数字）。
  var RE_LAW_NUM = /^\s*(?:[#>*\-+]\s*)*第\s*[〇零一二三四五六七八九十百千两0-9]{1,8}\s*条/;
  var RE_CLAUSE_NUM = /^\s*(?:[#>*\-+]\s*)*(\d{1,2}(?:\.\d{1,4}){1,5})(?=\s|\u3000|$|[^\d.])/;
  var RE_ANCHOR = /^(?:第\s*[〇零一二三四五六七八九十百千两0-9]{1,8}\s*条|\d{1,2}(?:\.\d{1,4}){1,5}|\d{1,2})$/;

  function detectLawNum(text) {
    var lines = String(text || "").split("\n");
    for (var i = 0; i < Math.min(lines.length, 4); i++) {
      var m = RE_LAW_NUM.exec(lines[i]);
      if (m) return m[0].replace(/\s+/g, "");
      m = RE_CLAUSE_NUM.exec(lines[i]);
      if (m) return m[1];
    }
    return "";
  }

  // 从原文行倒推：这块 span 覆盖了哪些条号（仅 UI 提示，如合并块覆盖多条，不改 15 字段）
  function coveredLawNums(chunk) {
    if (!chunk || !chunk.span || !state.doc || !state.doc.lines) return [];
    var nums = [];
    var start = Math.max(1, chunk.span[0]);
    var end = Math.min(state.doc.lines.length, chunk.span[1]);
    for (var n = start; n <= end; n++) {
      var line = state.doc.lines[n - 1];
      if (!line) continue;
      var m = RE_LAW_NUM.exec(line.text || "");
      var txt = m ? m[0].replace(/\s+/g, "") : "";
      if (!txt) {
        m = RE_CLAUSE_NUM.exec(line.text || "");
        if (m) txt = m[1];
      }
      if (!txt) continue;
      if (nums[nums.length - 1] !== txt) nums.push(txt);
    }
    return nums;
  }

  // 新增块的条号倒推：优先沿用前一块（同一块的续写），前面没有就看后一块。
  function clauseAnchorOf(chunk) {
    var value = String((chunk && chunk.law_num) || "").trim();
    if (!value || value === "（新增）") return "";
    var compact = value.replace(/\s+/g, "");
    if (!RE_ANCHOR.test(compact)) return "";
    if (compact.indexOf("表") >= 0 || compact.indexOf("附录") >= 0) return "";
    return compact;
  }

  function inferAddLawNum() {
    if (!state.doc || !state.doc.chunks.length) return "";
    var chunks = state.doc.chunks;
    var insertAt = chunks.length;
    if (state.addAfterId) {
      for (var i = 0; i < chunks.length; i++) {
        if (chunks[i].id === state.addAfterId) { insertAt = i + 1; break; }
      }
    }
    for (var j = Math.min(insertAt - 1, chunks.length - 1); j >= 0; j--) {
      var prevAnchor = clauseAnchorOf(chunks[j]);
      if (prevAnchor) return prevAnchor;
    }
    for (var k = insertAt; k < chunks.length; k++) {
      var nextAnchor = clauseAnchorOf(chunks[k]);
      if (nextAnchor) return nextAnchor;
    }
    return "";
  }

  // ---------- 表格新建切片的条号/表号推算（用相邻已切片交叉验证） ----------
  // law_num 形如「第二部分 裁量细则 表1 第214-214行」→ 前缀 + 行区间；
  // 只有表号没有行区间（如「附 录 C 表1」）时只返回前缀。
  var TABLE_LAW_RE = /^(.*?表\s*[0-9A-Za-z.．]+)\s*第\s*(\d+)\s*[-–—~～]\s*(\d+)\s*行\s*$/;

  function tableLawInfo(lawNum) {
    var value = String(lawNum || "").trim();
    if (!value || value === "（新增）") return null;
    var m = TABLE_LAW_RE.exec(value);
    if (m) {
      return {
        prefix: m[1].trim(),
        rowStart: parseInt(m[2], 10),
        rowEnd: parseInt(m[3], 10),
        hasRows: true
      };
    }
    if (value.indexOf("表") >= 0) return { prefix: value, rowStart: null, rowEnd: null, hasRows: false };
    return null;
  }

  function lawInfoOfTr(tr) {
    if (!tr || tr.dataset.chunk === undefined || !state.doc || !state.doc.chunks) return null;
    var chunk = state.doc.chunks[parseInt(tr.dataset.chunk, 10)];
    var info = tableLawInfo(chunk && chunk.law_num);
    if (!info) return null;
    return {
      idx: tr.sectionRowIndex,
      rowno: tr.dataset.rowno ? parseInt(tr.dataset.rowno, 10) : null,
      prefix: info.prefix,
      hasRows: info.hasRows
    };
  }

  // 沿兄弟行找最近的「已被切片覆盖」的行，取它的行号与表号前缀
  function nearestMappedRow(tr, dir) {
    var cur = tr;
    for (;;) {
      cur = dir < 0 ? cur.previousElementSibling : cur.nextElementSibling;
      if (!cur) return null;
      if (cur.dataset.rowno === undefined) continue;
      var info = lawInfoOfTr(cur);
      if (info && isFinite(info.rowno)) return info;
    }
  }

  function finishTableLaw(fromRow, toRow, above, below, confidence) {
    var side = above || below;
    if (!side || !isFinite(fromRow) || !isFinite(toRow) || fromRow < 1 || toRow < fromRow) return null;
    if (above && below && above.prefix !== below.prefix) {
      side = above; // 跨表时取上方一侧，置信度降级
      confidence = "medium";
    }
    var value = side.hasRows
      ? (side.prefix + " 第" + fromRow + "-" + toRow + "行")
      : side.prefix;
    return {
      value: value,
      confidence: confidence,
      note: confidence === "high" ? "" : "按相邻切片推算，请确认"
    };
  }

  // 用户选中的渲染表格行（tr 数组）→ { value, confidence, note }
  // 规则：上下各找最近的已切表格行，校验表号前缀一致、偏移一致、推算行号正好落在两邻居之间。
  function suggestTableLawNum(rows) {
    if (!rows || !rows.length || !state.doc) return null;
    var first = rows[0];
    var parent = first.parentElement;
    if (!parent || parent !== rows[rows.length - 1].parentElement) return null;
    var above = nearestMappedRow(first, -1);
    var below = nearestMappedRow(rows[rows.length - 1], 1);
    var fromIdx = first.sectionRowIndex;
    var toIdx = rows[rows.length - 1].sectionRowIndex;
    // 选中行本身已被切片覆盖：直接用它们自己的行号
    var selfRownos = [];
    var firstSelf = null;
    var lastSelf = null;
    for (var s = 0; s < rows.length; s++) {
      if (rows[s].dataset.rowno === undefined) continue;
      var info = lawInfoOfTr(rows[s]);
      if (!info) continue;
      selfRownos.push(info.rowno);
      if (!firstSelf) firstSelf = info;
      lastSelf = info;
    }
    if (selfRownos.length) {
      return finishTableLaw(Math.min.apply(null, selfRownos), Math.max.apply(null, selfRownos),
        firstSelf, lastSelf, "high");
    }
    if (!above && !below) return null;
    // 偏移 = 表格行下标 - 表内行号；上下两个邻居必须一致才可信
    var offset = null;
    if (above) offset = above.idx - above.rowno;
    if (below) {
      var offset2 = below.idx - below.rowno;
      if (offset === null) offset = offset2;
      else if (offset2 !== offset) {
        // 偏移不一致：取离选中行更近的一侧，置信度降级
        offset = (fromIdx - above.idx) <= (below.idx - toIdx) ? offset : offset2;
      }
    }
    if (offset === null) return null;
    var fromRow = fromIdx - offset;
    var toRow = toIdx - offset;
    // 交叉校验：推算结果必须严格落在两个邻居的行号之间
    if (above && fromRow <= above.rowno) return null;
    if (below && toRow >= below.rowno) return null;
    var confidence = above && below && (above.idx - above.rowno) === (below.idx - below.rowno) &&
      above.prefix === below.prefix ? "high" : "medium";
    return finishTableLaw(fromRow, toRow, above, below, confidence);
  }

  // 整块新增（没有选中行）时：块内表格切片的表号前缀唯一才预填
  function suggestTableLawNumForBlock(block) {
    if (!block || !state.doc || !state.doc.chunks) return null;
    var prefixes = [];
    state.doc.chunks.forEach(function (chunk) {
      if (!chunk.span || (chunk.chunk_kind !== "table" && chunk.chunk_kind !== "appendix")) return;
      if (chunk.span[1] < block.start || chunk.span[0] > block.end) return;
      var info = tableLawInfo(chunk.law_num);
      if (info && prefixes.indexOf(info.prefix) < 0) prefixes.push(info.prefix);
    });
    if (prefixes.length !== 1) return null;
    return { value: prefixes[0], confidence: "medium", note: "按该表格已有切片的表号推算，请确认" };
  }

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

  // 编辑会重建原文 DOM，表格内部的滚动容器（.tblwrap）默认回到顶部；
  // 这里在重渲染前后按「表格块起始行 / 卡片 id」记录并恢复内部 scrollTop。
  function captureTableWrapTops() {
    var tops = { source: {}, cards: {} };
    if (el.source) {
      el.source.querySelectorAll(".line.table-block").forEach(function (block) {
        var wrap = block.querySelector(".tblwrap");
        if (wrap && wrap.scrollTop) tops.source[block.dataset.line] = wrap.scrollTop;
      });
    }
    if (el.chunks) {
      el.chunks.querySelectorAll(".chunk-card").forEach(function (card) {
        var wrap = card.querySelector(".tblwrap");
        if (wrap && wrap.scrollTop) tops.cards[card.dataset.id] = wrap.scrollTop;
      });
    }
    return tops;
  }

  function restoreTableWrapTops(tops) {
    if (!tops) return;
    if (el.source) {
      el.source.querySelectorAll(".line.table-block").forEach(function (block) {
        var top = tops.source[block.dataset.line];
        if (!top) return;
        var wrap = block.querySelector(".tblwrap");
        if (wrap) wrap.scrollTop = top;
      });
    }
    if (el.chunks) {
      el.chunks.querySelectorAll(".chunk-card").forEach(function (card) {
        var top = tops.cards[card.dataset.id];
        if (!top) return;
        var wrap = card.querySelector(".tblwrap");
        if (wrap) wrap.scrollTop = top;
      });
    }
  }

  // 编辑后重渲染，尽量保持滚动位置与选中切片
  function applyDocPayload(doc) {
    hideSplitCaret();
    var activeId = state.activeChunk;
    var sourceTop = el.source.scrollTop;
    var chunksTop = el.chunks.scrollTop;
    var wrapTops = captureTableWrapTops();
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
    // 恢复表格内部滚动位置（放在 selectChunk 之后：删除/撤销这类没有新选中的操作保持原位置）
    restoreTableWrapTops(wrapTops);
    // 浏览器滚动锚定/卡片高度变化可能让 scrollTop 漂几像素，下一帧再对齐一次
    requestAnimationFrame(function () {
      el.source.scrollTop = sourceTop;
      el.chunks.scrollTop = chunksTop;
      restoreTableWrapTops(wrapTops);
    });
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

  function postOp(op, okMsg, afterFn) {
    if (!state.docKey) return;
    postJSON("/api/op", { key: state.docKey, op: op }).then(function (doc) {
      applyDocPayload(doc);
      if (afterFn) afterFn(doc);
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
    // 撤销/重做会改变块 id 的对应关系（新增会重排 id），清掉旧选中避免高亮到别的块
    state.activeChunk = null;
    postEditCmd("/api/undo", "已撤销");
  }

  function doRedo() {
    if (!state.doc || !state.doc.edits || !state.doc.edits.redo) return;
    state.activeChunk = null;
    postEditCmd("/api/redo", "已重做");
  }

  function doReset() {
    if (!state.doc || !state.doc.dirty) return;
    if (!window.confirm("丢弃本文件的全部手工修改，回到自动切块结果？")) return;
    state.activeChunk = null;
    postEditCmd("/api/reset", "已重置到自动切块结果");
  }

  function dropChunk(chunk) {
    if (!chunk) return;
    var droppedId = chunk.id;
    state.activeChunk = null;
    postOp({ op: "drop", id: droppedId }, "已删除 " + droppedId + "（Ctrl+Z 可撤销）", function () {
      // 删除后停留在原行：滚动位置由 applyDocPayload 保持，这里只补一句明确提示
      setStatus("已删除 " + droppedId + " · 原文与表格位置保持不变（Ctrl+Z 可撤销）", "ok");
    });
  }

  function mergeChunk(chunk, direction) {
    if (!state.doc) return;
    var chunks = state.doc.chunks;
    var index = chunks.indexOf(chunk);
    var withChunk = direction === "prev" ? chunks[index - 1] : chunks[index + 1];
    if (!withChunk) return;
    postOp({ op: "merge", id: chunk.id, with: withChunk.id }, "已合并 " + chunk.id + " 与 " + withChunk.id);
  }

  // 集体合并：把原文选区覆盖的连续多块合并成一块（保留第一块的 id / 条号 / kind）
  function mergeMany(chunks) {
    if (!state.doc || !chunks || chunks.length < 2) return;
    var ids = chunks.map(function (c) { return c.id; });
    postOp(
      { op: "merge_many", ids: ids },
      "已合并 " + ids.length + " 块（保留 " + ids[0] + "，Ctrl+Z 可撤销）",
      function (doc) {
        for (var i = 0; i < doc.chunks.length; i++) {
          if (doc.chunks[i].id === ids[0]) {
            selectChunk(doc.chunks[i], { scrollSource: true, scrollCard: true });
            break;
          }
        }
      }
    );
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
    el.splitLawNum.value = "";
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
    updateSplitHint();
    el.splitDialog.showModal();
  }

  function updateSplitHint() {
    var chunk = state.pendingChunk;
    if (!chunk) return;
    var body = String(chunk.chunk || "");
    var lines = body.split("\n");
    var lineSplit = lines.length > 1;
    var k = 1;
    var atChar = 0;
    if (lineSplit) {
      var checked = el.splitLines.querySelector("input[name=split-at]:checked");
      k = checked ? parseInt(checked.value, 10) : 1;
    } else {
      atChar = parseInt(el.splitChar.value, 10);
      if (!isFinite(atChar)) atChar = Math.max(1, Math.floor(body.length / 2));
    }
    var secondText = lineSplit ? lines.slice(k).join("\n") : body.slice(atChar);
    var detected = detectLawNum(secondText);
    // 正文里没带条号时，再看切点附近的原文行（合并过的块：正文行和源行不一定一一对应，往后扫几行）
    if (!detected && chunk.span && state.doc && state.doc.lines) {
      var base = chunk.span[0] - 1 + (lineSplit ? k : 0);
      for (var off = 0; off <= 4; off++) {
        var line = state.doc.lines[base + off];
        if (!line) break;
        var d = detectLawNum(line.text);
        if (d) { detected = d; break; }
      }
    }
    el.splitLawNum.placeholder = detected
      ? "自动识别：" + detected + "（留空即可）"
      : "留空则自动识别切点所在原文行的条号；识别不到继承原块：" + (chunk.law_num || "（无）");
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
    var secondLawNum = String(el.splitLawNum.value || "").trim();
    if (secondLawNum) op.second_law_num = secondLawNum;
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

  // 新增块的插入位置（表格行专用）：同一行可能有几百个块，不能按「覆盖该行的最后一块」判断。
  // 规则：插在选中行上方最近一个已切表格行对应的块之后；上方没有已切行 → 插到该表格块第一块之前。
  function tableRowInsertAnchor(rows) {
    if (!rows || !rows.length || !state.doc) return {};
    var cur = rows[0];
    while ((cur = cur.previousElementSibling)) {
      if (cur.dataset.chunk !== undefined) {
        var aboveChunk = state.doc.chunks[parseInt(cur.dataset.chunk, 10)];
        if (aboveChunk) return { afterId: aboveChunk.id };
      }
    }
    var blockRow = rows[0].closest(".line.table-block");
    var block = blockRow ? tableBlockAt(parseInt(blockRow.dataset.line, 10)) : null;
    if (!block) return {};
    for (var i = 0; i < state.doc.chunks.length; i++) {
      var c = state.doc.chunks[i];
      if (c.span && c.span[1] >= block.start && c.span[0] <= block.end) return { beforeId: c.id };
    }
    return {};
  }

  // 块签名：用于在 add 之后从新列表里认出「刚新增的那一块」（不依赖 diff，因为删除后重建
  // 会与基线里被删的块内容相似，diff 可能把它判成 modified，added_ids 为空）
  function chunkSignature(chunk) {
    return [chunk.chunk || "", chunk.law_num || "", chunk.chunk_kind || ""].join("\u0001");
  }

  function openAdd(picked) {
    if (!state.doc) { showToast("先打开一个文件", "warn"); return; }
    state.opErrEl = el.addErrors;
    el.addErrors.hidden = true;
    el.addErrors.textContent = "";

    // 如果原文里有选区（或右键菜单带入了选区）：把选区文本带进弹层，并记下行区间
    if (!picked || typeof picked.start !== "number") picked = selectedText();
    state.addBeforeId = null;
    if (picked && picked.text.trim()) {
      state.addSpan = [picked.start, picked.end];
      el.addText.value = picked.text;
      if (picked.tableRowsArr && picked.tableRowsArr.length) {
        // 表格行新增：按行顺序定位插入点（不能用块内 span 相交判断——同一行可能有几百个块）
        var anchor = tableRowInsertAnchor(picked.tableRowsArr);
        state.addAfterId = anchor.afterId || null;
        if (anchor.beforeId) state.addBeforeId = anchor.beforeId;
      } else {
        var hits = chunksInLineRange(picked.start, picked.end);
        if (hits.length) {
          state.addAfterId = hits[hits.length - 1].id;
        } else {
          // 选区落在没有切片的区域（标题/目录/缺口）：插到它前面最近的切片之后；
          // 顶部缺口（比所有切片都靠前）则插到第一块之前
          var prevChunk = null;
          var firstChunk = null;
          for (var pi = 0; pi < state.doc.chunks.length; pi++) {
            var pc = state.doc.chunks[pi];
            if (!firstChunk && pc.span) firstChunk = pc;
            if (pc.span && pc.span[1] < picked.start) prevChunk = pc;
          }
          state.addAfterId = prevChunk ? prevChunk.id : null;
          if (!prevChunk && firstChunk) state.addBeforeId = firstChunk.id;
        }
      }
      var rangeLabel = picked.start === picked.end
        ? "第 " + picked.start + " 行"
        : "第 " + picked.start + "–" + picked.end + " 行";
      if (picked.tableRows) {
        var trLabel = picked.tableRows[0] === picked.tableRows[1]
          ? ("表格内第 " + picked.tableRows[0] + " 行")
          : ("表格内第 " + picked.tableRows[0] + "–" + picked.tableRows[1] + " 行");
        el.addHint.textContent = "已带入" + trLabel + "（" + picked.text.length + " 字" +
          (picked.tableGap ? "，这部分没有切片覆盖" : "") + "）；行区间记到整个表格块（原文" +
          rangeLabel + "），新增块会定位到该表格块的位置。";
      } else {
        el.addHint.textContent = "已带入原文" + rangeLabel + "的选区（" + picked.text.length +
          " 字），新增块会定位到这几行；可继续编辑正文。";
      }
      el.addHint.hidden = false;
    } else {
      state.addSpan = null;
      el.addText.value = "";
      state.addAfterId = state.activeChunk || null;
      el.addHint.textContent = "先在中间原文里拖选一段文字，点「新增」可直接把选区变成切片；也可以直接输入正文手动新增。";
      el.addHint.hidden = false;
    }
    el.addLawNum.value = "";
    // 来源是表格：用相邻已切片的条号/表号预填（可改）
    var suggestion = null;
    if (picked && picked.tableRowsArr && picked.tableRowsArr.length) {
      suggestion = suggestTableLawNum(picked.tableRowsArr);
    } else if (picked && picked.tableBlockRange) {
      suggestion = suggestTableLawNumForBlock(picked.tableBlockRange);
    }
    var inferredAddNum = inferAddLawNum();
    if (suggestion) {
      el.addLawNum.value = suggestion.value;
      el.addHint.textContent += "；条号已预填「" + suggestion.value + "」" +
        (suggestion.note ? "（" + suggestion.note + "）" : "") + "，可修改。";
      el.addHint.hidden = false;
    }
    el.addLawNum.placeholder = inferredAddNum
      ? ("留空则按前后块倒推：" + inferredAddNum)
      : "留空则为「（新增）」";
    // 来源是表格行 / 表格块：类型默认「表格」，其余默认「段落」（弹层里仍可改）
    var fromTable = !!(picked && (picked.tableRowsArr || picked.tableBlockRange));
    fillKind(el.addKind, fromTable ? "table" : "paragraph");
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
    if (state.addBeforeId) op.before_id = state.addBeforeId;
    if (state.addSpan) op.span = [state.addSpan[0], state.addSpan[1]];
    // 记录提交前的块签名，用于在返回结果里认出刚新增的那一块
    var beforeSig = {};
    (state.doc.chunks || []).forEach(function (c) { beforeSig[chunkSignature(c)] = true; });
    postJSON("/api/op", { key: state.docKey, op: op }).then(function (doc) {
      el.addDialog.close();
      var hadSpan = !!op.span;
      state.addSpan = null;
      state.addBeforeId = null;
      applyDocPayload(doc);
      // 新建的切片要立刻高亮：优先 diff.added_ids；删除后重建等场景 diff 可能判成 modified，
      // 此时用「签名不在提交前集合里」的块兜底，避免看起来「建完没反应」
      var addedIds = (doc.diff && doc.diff.added_ids) || [];
      var addedChunk = addedIds.length ? findChunk(addedIds[0]) : null;
      if (!addedChunk) {
        for (var ai = 0; ai < doc.chunks.length; ai++) {
          if (!beforeSig[chunkSignature(doc.chunks[ai])]) { addedChunk = doc.chunks[ai]; break; }
        }
      }
      if (addedChunk) {
        selectChunk(addedChunk, { scrollSource: true, scrollCard: true });
      }
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
    el.splitLines.addEventListener("change", updateSplitHint);
    el.splitChar.addEventListener("input", updateSplitHint);
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
        hideSplitCaret();
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
    // 点菜单外部 / 滚动原文或切片 / 窗口尺寸变化时收起右键菜单与拆分光标
    document.addEventListener("click", function (ev) {
      if (!el.ctxMenu.hidden && !el.ctxMenu.contains(ev.target)) hideCtxMenu();
      if (el.source && !el.source.contains(ev.target)) hideSplitCaret();
    });
    el.source.addEventListener("scroll", function () {
      if (!programmaticScrollNow()) hideCtxMenu();
      hideSplitCaret();
    });
    el.chunks.addEventListener("scroll", function () {
      if (!programmaticScrollNow()) hideCtxMenu();
    });
    window.addEventListener("resize", function () { hideCtxMenu(); hideSplitCaret(); });
    window.addEventListener("hashchange", function () {
      var key = keyFromHash();
      if (key && key !== state.docKey) selectByKey(key);
    });
  }

  bindEvents();
  initHealth();
  loadFiles();
})();
