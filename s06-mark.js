/* S06 carton-mark PDF — same layout/rules as 箱唛填写工作台 */
(function (global) {
  var TPL = {
    AU:  { code: "AU",  dest: "MEL", faces: 4 },
    QD:  { code: "QD",  dest: "QD",  faces: 4 },
    POL: { code: "POL", dest: "POL", faces: 2 },
    USA: { code: "USA", dest: "USA", faces: 2 }
  };
  var TPL_ORDER = ["AU", "QD", "POL", "USA"];

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function parseNum(text) {
    var m = String(text == null ? "" : text).replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
    return m ? Number(m[0]) : NaN;
  }
  function parseDims(text) {
    var p = String(text || "").replace(/cm|mm/ig, " ").split(/[^0-9.]+/).filter(function (x) {
      return x && !isNaN(x);
    }).map(Number);
    return p.length >= 3 ? p.slice(0, 3) : null;
  }
  function dimsFormatOk(text) {
    var s = String(text || "").replace(/\s*c\.?m\.?\s*$/i, "").trim();
    return /^\d+(?:\.\d+)?\s*\*\s*\d+(?:\.\d+)?\s*\*\s*\d+(?:\.\d+)?$/.test(s);
  }
  function withCm(text) {
    var s = String(text || "").replace(/\s*c\.?m\.?\s*$/i, "").trim();
    return s ? s + " CM" : "";
  }
  function withKg(text) {
    var n = parseNum(text);
    if (isNaN(n)) return "";
    return n.toFixed(2) + " KG";
  }
  function withPcs(text) {
    var s = String(text || "").replace(/\s*(pcs?|pieces?)\s*$/i, "").trim();
    return s ? s + " PCS" : "";
  }
  function isLargeBox(dims) {
    if (!dims) return null;
    var a = dims.slice().sort(function (x, y) { return y - x; });
    return a[0] >= 30 && a[1] >= 30 && a[2] >= 20;
  }
  function layoutOf(job) {
    if (job.layout === "2" || job.layout === "4") return job.layout;
    if (job.dims && !dimsFormatOk(job.dims)) return "";
    var large = isLargeBox(parseDims(job.dims));
    if (large === true) return "2";
    if (large === false) return "4";
    return "";
  }
  function detectTemplate(port) {
    var s = String(port || "").toUpperCase().replace(/\s+/g, "");
    if (/USA|UNITEDSTATES|\bUS\b/.test(s)) return TPL.USA;
    if (/QINGDAO|青岛/.test(s) || s === "QD") return TPL.QD;
    if (/POLAND|波兰|\bPOL\b|^PL$/.test(s)) return TPL.POL;
    if (/MELBOURNE|AUSTRALIA|澳大利亚|澳洲|\bMEL\b|\bAUS\b|^AU$/.test(s)) return TPL.AU;
    if (s.indexOf("QD") >= 0) return TPL.QD;
    if (s.indexOf("USA") >= 0) return TPL.USA;
    if (s.indexOf("POL") >= 0) return TPL.POL;
    if (s.indexOf("AU") >= 0 || s.indexOf("MEL") >= 0) return TPL.AU;
    return null;
  }
  function cartonRange(job) {
    var total = parseInt(job.cartonTotal, 10);
    if (isNaN(total) || total < 1) total = 1;
    if (job.isFullSplit || job.isTail) {
      var from = parseInt(job.cartonFrom, 10);
      var to = parseInt(job.cartonTo, 10);
      if (isNaN(from) || from < 1) from = 1;
      if (isNaN(to) || to < from) to = from;
      return { from: from, to: to, total: total };
    }
    return { from: 1, to: total, total: total };
  }
  function markHtml(job, cartonNo, cartonTotal, size) {
    var tpl = TPL[job.tpl] || TPL.QD;
    var dest = tpl.dest;
    var n = cartonNo == null ? (job.cartonFrom || "") : cartonNo;
    var t = cartonTotal == null ? (job.cartonTotal || "") : cartonTotal;
    return ''
      + '<div class="mark' + (size === "4" ? " sz4" : "") + '">'
      + '  <div class="mk-top">'
      + '    <div class="mk-left">'
      + '      <div class="mk-box" style="flex:1.15"><div class="lab">SUPPLIER</div><div class="val sm">ALPHA FASHION CO.,LIMITED</div></div>'
      + '      <div class="mk-split" style="flex:1.05">'
      + '        <div class="mk-box"><div class="lab">BRAND NAME</div><div class="val">LOVISA</div></div>'
      + '        <div class="mk-box"><div class="lab">DESTINATION</div><div class="val dest-val">' + esc(dest) + '</div></div>'
      + '      </div>'
      + '      <div class="mk-box" style="flex:1.05"><div class="lab">RANGE</div><div class="val">' + esc(job.range || "") + '</div></div>'
      + '      <div class="mk-box" style="flex:1.15"><div class="lab">PURCHASE ORDER NUMBER</div><div class="val">' + esc(job.po || "") + '</div></div>'
      + '    </div>'
      + '    <div class="mk-right"></div>'
      + '  </div>'
      + '  <div class="mk-bottom">'
      + '    <div class="mk-col w25">'
      + '      <div class="mk-box barcode-box" style="flex:1.25"><div class="lab">BARCODE</div><div class="val barcode-val"></div></div>'
      + '      <div class="mk-box"><div class="lab">DIMMENSIONS</div><div class="val sm">' + esc(withCm(job.dims)) + '</div></div>'
      + '      <div class="mk-box"><div class="lab">GROSS WEIGHT</div><div class="val sm">' + esc(withKg(job.gw)) + '</div></div>'
      + '    </div>'
      + '    <div class="mk-col w75">'
      + '      <div class="mk-box" style="flex:1.35"><div class="lab">REFERENCE NUMBER</div><div class="val">' + esc(job.ho || "") + '</div></div>'
      + '      <div class="mk-box" style="flex:1.2"><div class="lab">QUANTITY IN CARTON</div><div class="val">' + esc(withPcs(job.qtyCtn)) + '</div></div>'
      + '      <div class="mk-box qty-head" style="flex:0.5"><div class="lab">QUANTITY</div></div>'
      + '      <div class="carton-row">'
      + '        <div class="cn-lab">CARTON NUMBER</div>'
      + '        <div class="cn-n">' + esc(n) + '</div>'
      + '        <div class="cn-of">OF</div>'
      + '        <div class="cn-t">' + esc(t) + '</div>'
      + '      </div>'
      + '    </div>'
      + '  </div>'
      + '</div>';
  }
  function cmpJobs(a, b) {
    var ia = TPL_ORDER.indexOf(a.tpl); if (ia < 0) ia = 99;
    var ib = TPL_ORDER.indexOf(b.tpl); if (ib < 0) ib = 99;
    if (ia !== ib) return ia - ib;
    var ha = String(a.ho || ""), hb = String(b.ho || "");
    if (ha !== hb) return ha < hb ? -1 : ha > hb ? 1 : 0;
    return String(a.po || "").localeCompare(String(b.po || ""));
  }
  function expandCopies(list) {
    var out = [];
    list.slice().sort(cmpJobs).forEach(function (job) {
      var tpl = TPL[job.tpl];
      if (!tpl) return;
      var rg = cartonRange(job);
      var L = layoutOf(job) || "2";
      var faces = tpl.faces || 1;
      var n, f;
      for (n = rg.from; n <= rg.to; n++) {
        for (f = 1; f <= faces; f++) {
          out.push({ job: job, n: n, total: rg.total, layout: L, tpl: job.tpl });
        }
      }
    });
    return out;
  }
  function packSheets(copies) {
    var sheets = [], twoUp = [], fourFace = [], twoFace4 = [], i;
    for (i = 0; i < copies.length; i++) {
      var c = copies[i];
      if (c.layout === "4") {
        var t = TPL[c.tpl];
        if (t && t.faces === 2) twoFace4.push(c);
        else fourFace.push(c);
      } else twoUp.push(c);
    }
    function packByTpl(arr, layout, per) {
      TPL_ORDER.forEach(function (code) {
        var group = arr.filter(function (x) { return x.tpl === code; });
        var k = 0;
        while (k < group.length) {
          var batch = [];
          while (k < group.length && batch.length < per) { batch.push(group[k]); k++; }
          if (batch.length) sheets.push({ layout: layout, batch: batch });
        }
      });
    }
    packByTpl(twoUp, "2", 2);
    packByTpl(fourFace, "4", 4);
    i = 0;
    while (i < twoFace4.length) {
      sheets.push({ layout: "4", batch: twoFace4.slice(i, i + 4) });
      i += 4;
    }
    return sheets;
  }
  function emitPage(layout, batch) {
    if (layout === "4") {
      var n = batch.length;
      var cls = "sheet sheet-4" + (n <= 2 ? " pair" : "");
      var html = '<div class="' + cls + '">';
      if (n > 2) html += '<div class="cut-h"></div>';
      if (n > 1) html += '<div class="cut-v"></div>';
      for (var i = 0; i < n; i++) {
        html += '<div class="cell">' + markHtml(batch[i].job, batch[i].n, batch[i].total, "4") + "</div>";
      }
      return html + "</div>";
    }
    var h = '<div class="sheet sheet-2"><div class="cut-h"></div>';
    batch.forEach(function (c) { h += markHtml(c.job, c.n, c.total, "2"); });
    return h + "</div>";
  }
  function fitOverflow(root) {
    if (!root) return;
    var marks = root.querySelectorAll(".mark");
    for (var m = 0; m < marks.length; m++) {
      var mark = marks[m];
      var is4 = (" " + mark.className + " ").indexOf(" sz4 ") >= 0;
      var nodes = mark.querySelectorAll(".val, .cn-n, .cn-t, .lab, .cn-lab");
      for (var i = 0; i < nodes.length; i++) {
        var el = nodes[i];
        if (!(el.textContent || "").trim()) continue;
        var cls = " " + el.className + " ";
        var isLab = cls.indexOf(" lab ") >= 0 || cls.indexOf(" cn-lab ") >= 0;
        var base = isLab ? (is4 ? 7.2 : 9.6) : (is4 ? 18 : 24);
        var minPt = isLab ? (is4 ? 5.5 : 7) : (is4 ? 9 : 11);
        var pt = base, steps = 0;
        el.style.fontSize = pt + "pt";
        while (el.scrollWidth > el.clientWidth + 1 && pt > minPt && steps < 40) {
          pt = Math.round((pt - 0.5) * 10) / 10;
          el.style.fontSize = pt + "pt";
          steps++;
        }
      }
    }
  }
  function ensureRoot() {
    var root = document.getElementById("s06-print-root");
    if (!root) {
      root = document.createElement("div");
      root.id = "s06-print-root";
      document.body.appendChild(root);
    }
    return root;
  }
  function showMask(text) {
    var mask = document.getElementById("s06PdfMask");
    if (!mask) {
      mask = document.createElement("div");
      mask.id = "s06PdfMask";
      mask.className = "modal on";
      mask.innerHTML = '<div class="box dlg-box"><div class="hd"><span>正在导出S06箱唛PDF</span></div><div class="bd"><div id="s06PdfMaskMsg"></div></div></div>';
      document.body.appendChild(mask);
    }
    mask.className = "modal on";
    mask.style.display = "flex";
    var msg = document.getElementById("s06PdfMaskMsg");
    if (msg) msg.textContent = text || "请稍候…";
  }
  function hideMask() {
    var mask = document.getElementById("s06PdfMask");
    if (mask) { mask.className = "modal"; mask.style.display = "none"; }
  }
  function intOf(v, fallback) {
    var n = parseInt(String(v == null ? "" : v).replace(/,/g, ""), 10);
    return isNaN(n) || n < 1 ? (fallback == null ? 1 : fallback) : n;
  }
  function rowDims(row) {
    var L = String(row.packlong || "").trim();
    var W = String(row.packwidth || "").trim();
    var H = String(row.packhigh || "").trim();
    if (L && W && H) return L + "*" + W + "*" + H;
    var s = String(row.cartonSize || "").trim().replace(/[xX×／/]/g, "*");
    return s;
  }
  function rowToJob(row, from, to, total, isTail) {
    var det = detectTemplate(row.country || row.port || "");
    var dims = rowDims(row);
    return {
      id: String(row.gicode || row.id || "").trim(),
      po: String(row.po || "").trim(),
      ho: String(row.ho || "").trim(),
      range: String(row.range || "").trim(),
      qtyOrder: String(row.qtyOrder || row.salqtc || "").trim(),
      tpl: det ? det.code : "",
      dims: dims,
      gw: String(row.gross || "").trim(),
      qtyCtn: String(row.perCarton || "").trim(),
      cartonFrom: String(from),
      cartonTo: String(to),
      cartonTotal: String(total),
      layout: "auto",
      isTail: !!isTail,
      isFullSplit: !isTail && Number(from) !== Number(to),
      country: String(row.country || "").trim()
    };
  }
  function packingToJobs(rows) {
    var usable = (rows || []).filter(function (r) {
      var kind = String(r.packType || "");
      if (kind.indexOf("拆行") >= 0) return false;
      return true;
    });
    var groups = {};
    usable.forEach(function (r) {
      var k = [r.po || "", r.ho || "", r.country || ""].join("|");
      if (!groups[k]) groups[k] = { mains: [], tails: [] };
      if (String(r.packType || "").indexOf("尾箱") >= 0) groups[k].tails.push(r);
      else groups[k].mains.push(r);
    });
    var jobs = [];
    Object.keys(groups).forEach(function (k) {
      var g = groups[k];
      var singleMain = g.mains.length === 1;
      var tailQ = 0;
      g.tails.forEach(function (t) { tailQ += intOf(t.qtp, 1); });
      g.mains.forEach(function (r) {
        var q = intOf(r.qtp, 1);
        var total = (singleMain && tailQ) ? q + tailQ : q;
        jobs.push(rowToJob(r, 1, q, total, false));
      });
      if (singleMain && g.tails.length) {
        var q = intOf(g.mains[0].qtp, 1);
        var total = q + tailQ;
        var from = q + 1;
        g.tails.forEach(function (t) {
          var tq = intOf(t.qtp, 1);
          jobs.push(rowToJob(t, from, from + tq - 1, total, true));
          from += tq;
        });
      } else {
        g.tails.forEach(function (t) {
          var tq = intOf(t.qtp, 1);
          jobs.push(rowToJob(t, 1, tq, tq, true));
        });
      }
    });
    return jobs;
  }
  function factoryNameOf(rows) {
    var r = (rows || []).find(function (x) { return String(x.supplierName || "").trim(); });
    if (!r) r = (rows || [])[0];
    var name = String((r && (r.supplierName || r.supplier)) || "").trim();
    if (name.indexOf(":") >= 0) name = name.split(":").slice(1).join(":").trim();
    return name || "箱唛";
  }
  function fileTitle(rows) {
    var d = new Date();
    var mm = String(d.getMonth() + 1).padStart(2, "0");
    var dd = String(d.getDate()).padStart(2, "0");
    return "S06箱唛-DD" + mm + "." + dd + "-" + factoryNameOf(rows);
  }

  function exportPdf(rows, opts) {
    opts = opts || {};
    var jobs = packingToJobs(rows);
    var noTpl = jobs.filter(function (j) { return !j.tpl; });
    if (noTpl.length) {
      return Promise.reject(new Error("有 " + noTpl.length + " 条目的港无法识别箱唛模板（AU/QD/POL/USA）。"));
    }
    var noLayout = jobs.filter(function (j) { return !layoutOf(j); });
    if (noLayout.length) {
      return Promise.reject(new Error("有 " + noLayout.length + " 条尺寸无法判定一开二/一开四。"));
    }
    if (!jobs.length) return Promise.reject(new Error("没有可导出的箱唛行（已跳过拆行）。"));
    var hasLib = typeof html2canvas === "function" && window.jspdf && window.jspdf.jsPDF;
    if (!hasLib) return Promise.reject(new Error("箱唛PDF组件未加载，请刷新后再试。"));
    var copies = expandCopies(jobs);
    if (!copies.length) return Promise.reject(new Error("无法生成箱唛页。"));
    var root = ensureRoot();
    var html = "";
    packSheets(copies).forEach(function (sheet) { html += emitPage(sheet.layout, sheet.batch); });
    root.innerHTML = html;
    fitOverflow(root);
    var sheets = [].slice.call(root.querySelectorAll(".sheet"));
    showMask("正在生成PDF（0/" + sheets.length + " 页）…");
    root.className = "pdf-capture";
    var JsPDF = window.jspdf.jsPDF;
    var pdf = null;
    var i = 0;
    return new Promise(function (resolve, reject) {
      function cleanup() {
        for (var s = 0; s < sheets.length; s++) sheets[s].style.display = "";
        root.className = "";
        hideMask();
      }
      function fail(err) {
        cleanup();
        reject(err);
      }
      function next() {
        if (i >= sheets.length) {
          try {
            var name = (opts.fileName || fileTitle(rows)) + ".pdf";
            pdf.save(name);
            cleanup();
            resolve({ pages: sheets.length, jobs: jobs.length, name: name });
          } catch (e) { fail(e); }
          return;
        }
        var sheet = sheets[i];
        var land = (" " + sheet.className + " ").indexOf(" sheet-4 ") >= 0;
        var s;
        for (s = 0; s < sheets.length; s++) sheets[s].style.display = s === i ? "" : "none";
        var msg = document.getElementById("s06PdfMaskMsg");
        if (msg) msg.textContent = "正在生成PDF（" + i + "/" + sheets.length + " 页）…";
        html2canvas(sheet, { scale: 2, backgroundColor: "#ffffff", useCORS: true, logging: false }).then(function (canvas) {
          var img = canvas.toDataURL("image/jpeg", 0.93);
          var orient = land ? "landscape" : "portrait";
          var w = land ? 297 : 210;
          var h = land ? 210 : 297;
          if (!pdf) pdf = new JsPDF({ orientation: orient, unit: "mm", format: "a4", compress: true });
          else pdf.addPage("a4", orient);
          pdf.addImage(img, "JPEG", 0, 0, w, h);
          i++;
          setTimeout(next, 0);
        }).catch(fail);
      }
      setTimeout(next, 50);
    });
  }

  function gwOverLimit(text) {
    var n = parseNum(text);
    return !isNaN(n) && n > 13.0001;
  }
  function dimsMinOf(tpl) {
    if (tpl === "QD") return 15;
    if (tpl === "AU" || tpl === "POL" || tpl === "USA") return 13;
    return 0;
  }
  function dimsTooSmall(job) {
    if (!job || !job.dims || !dimsFormatOk(job.dims)) return null;
    var min = dimsMinOf(job.tpl);
    if (!min) return null;
    var p = parseDims(job.dims);
    if (!p) return null;
    for (var i = 0; i < 3; i++) {
      if (p[i] < min) {
        return job.tpl === "QD" ? "QD纸箱尺寸不能小于15*15*15" : "AU/POL/USA纸箱尺寸不能小于13*13*13";
      }
    }
    return null;
  }
  function factoryMissing(job) {
    var miss = [];
    if (!String(job.dims || "").trim()) miss.push("DIMMENSIONS");
    if (!String(job.gw || "").trim()) miss.push("GROSS WEIGHT");
    if (!String(job.qtyCtn || "").trim()) miss.push("QUANTITY IN CARTON");
    if (!String(job.cartonFrom || "").trim()) miss.push("CARTON NUMBER");
    if (!String(job.cartonTotal || "").trim()) miss.push("总箱数");
    if (!job.tpl) miss.push("模板");
    return miss;
  }
  function gwFormatOk(text) {
    var s = String(text || "").replace(/\s*kgs?\s*$/i, "").trim();
    if (!s) return true;
    return /^\d+(?:\.\d+)?$/.test(s);
  }
  function qtyFormatOk(text) {
    var s = String(text || "").replace(/\s*(pcs?|pieces?)\s*$/i, "").trim();
    if (!s) return true;
    return /^\d+$/.test(s) && Number(s) > 0;
  }
  function intFormatOk(text) {
    var s = String(text || "").trim();
    if (!s) return true;
    return /^\d+$/.test(s) && Number(s) > 0;
  }
  function factoryFormatErrors(job) {
    var errs = [];
    if (job.dims && !dimsFormatOk(job.dims)) errs.push("DIMMENSIONS必须用 * 连接，例如 52*36*20");
    if (job.gw && !gwFormatOk(job.gw)) errs.push("GROSS WEIGHT格式不正确");
    if (job.qtyCtn && !qtyFormatOk(job.qtyCtn)) errs.push("QUANTITY IN CARTON必须为正整数");
    if (job.cartonFrom && !intFormatOk(job.cartonFrom)) errs.push("CARTON NUMBER必须为正整数");
    if (job.cartonTotal && !intFormatOk(job.cartonTotal)) errs.push("总箱数必须为正整数");
    if (job.cartonFrom && job.cartonTotal && intFormatOk(job.cartonFrom) && intFormatOk(job.cartonTotal)
        && parseInt(job.cartonFrom, 10) > parseInt(job.cartonTotal, 10)) {
      errs.push("CARTON NUMBER不能大于总箱数");
    }
    return errs;
  }
  function qtyMismatch(job) {
    if (!job || job.isFullSplit || job.isTail) return null;
    var order = parseNum(job.qtyOrder);
    var per = parseNum(job.qtyCtn);
    var boxes = parseNum(job.cartonTotal);
    if (isNaN(order) || isNaN(per) || isNaN(boxes)) return null;
    var prod = per * boxes;
    if (Math.abs(prod - order) < 0.0001) return null;
    return { order: order, per: per, boxes: boxes, prod: prod };
  }
  function tailPlan(job) {
    if (!job || job.isTail || job.isFullSplit) return null;
    var order = Math.round(parseNum(job.qtyOrder));
    var per = Math.round(parseNum(job.qtyCtn));
    if (!isFinite(order) || !isFinite(per) || order <= 0 || per <= 0) return null;
    if (per >= order) return null;
    var rem = order % per;
    if (rem === 0) return null;
    var full = Math.floor(order / per);
    if (full < 1) return null;
    return { order: order, per: per, full: full, rem: rem, total: full + 1 };
  }
  function whoOf(job) {
    return job.ho || job.po || "未知名细";
  }
  function validateJobs(jobs) {
    var errors = [];
    var mismatch = [];
    (jobs || []).forEach(function (job) {
      var who = whoOf(job);
      var miss = factoryMissing(job);
      if (miss.length) errors.push(who + " 缺少 " + miss.join("、"));
      factoryFormatErrors(job).forEach(function (e) { errors.push(who + " " + e); });
      if (gwOverLimit(job.gw)) errors.push(who + " 毛重不可超过13.00KG");
      var dimErr = dimsTooSmall(job);
      if (dimErr) errors.push(who + " " + dimErr);
      var qn = parseNum(job.qtyCtn);
      var on = parseNum(job.qtyOrder);
      if (!isNaN(qn) && !isNaN(on) && qn > on) errors.push(who + " 每箱数量不能大于签约数");
      var packedBoxes = parseNum(job.cartonTotal);
      if (!isNaN(qn) && !isNaN(packedBoxes) && !isNaN(on) && qn * packedBoxes > on + 0.0001) {
        errors.push(who + " 装箱总数量不能大于签约数");
      }
      if (job.dims && !layoutOf(job)) errors.push(who + " 无法判定排版（尺寸必须 长*宽*高）");
      var plan = tailPlan(job);
      if (plan) {
        var hasTail = jobs.some(function (j) {
          return j.isTail && j.po === job.po && j.ho === job.ho && j.country === job.country;
        });
        if (!hasTail) {
          errors.push(who + " 签约数不能整除每箱，装箱明细需有尾箱行（满箱 " + plan.full + " × " + plan.per + "，尾箱 " + plan.rem + "）");
        }
      }
      var mm = qtyMismatch(job);
      if (mm && !job.qtyMismatchOk) mismatch.push({ job: job, mm: mm });
    });
    return { errors: errors, mismatch: mismatch, jobs: jobs };
  }
  function ensureReviewDom() {
    if (document.getElementById("s06-review-mask")) return;
    var wrap = document.createElement("div");
    wrap.innerHTML = ''
      + '<div class="s06-review-mask" id="s06-review-mask">'
      + '  <div class="s06-review-top">'
      + '    <div class="ttl">S06 箱唛预览核对</div>'
      + '    <div class="s06-review-rule" id="s06-review-rule"></div>'
      + '    <button class="btn" type="button" id="s06-review-zoom">放大镜</button>'
      + '    <button class="btn btn-plain" type="button" id="s06-review-back">返回修改</button>'
      + '    <button class="btn btn-blue" type="button" id="s06-review-ok">确认OK</button>'
      + '  </div>'
      + '  <div class="s06-review-body" id="s06-review-body"></div>'
      + '</div>'
      + '<div class="s06-zoom-mask" id="s06-zoom-mask">'
      + '  <div class="s06-zoom-view" id="s06-zoom-view"></div>'
      + '  <div class="s06-zoom-hint">点击空白处关闭放大</div>'
      + '</div>';
    while (wrap.firstChild) document.body.appendChild(wrap.firstChild);
  }
  function closeZoom() {
    var z = document.getElementById("s06-zoom-mask");
    if (z) z.className = "s06-zoom-mask";
  }
  function openZoom(slot) {
    if (!slot) return;
    var z = document.getElementById("s06-zoom-view");
    var mask = document.getElementById("s06-zoom-mask");
    if (!z || !mask) return;
    var land = (" " + slot.className + " ").indexOf(" land ") >= 0;
    z.className = "s06-zoom-view " + (land ? "land" : "port");
    var sheet = slot.querySelector(".sheet");
    z.innerHTML = sheet ? sheet.outerHTML : slot.innerHTML;
    mask.className = "s06-zoom-mask show";
    requestAnimationFrame(function () { fitOverflow(z); });
  }
  function closeReview() {
    var m = document.getElementById("s06-review-mask");
    if (m) m.className = "s06-review-mask";
    closeZoom();
  }
  function renderReview(jobs) {
    ensureReviewDom();
    var copies = expandCopies(jobs);
    if (!copies.length) throw new Error("无法生成预览图：请检查模板和箱号。");
    var has2 = false, has4 = false, i;
    for (i = 0; i < copies.length; i++) {
      if (copies[i].layout === "4") has4 = true;
      else has2 = true;
    }
    var rule = [];
    if (has2) rule.push("纸箱尺寸大于30*30*20，A4纸一开二");
    if (has4) rule.push("纸箱尺寸小于30*30*20，A4纸一开四");
    var ruleEl = document.getElementById("s06-review-rule");
    if (ruleEl) ruleEl.textContent = rule.join("；");
    var html = "";
    packSheets(copies).forEach(function (sheet) {
      var L = sheet.layout;
      var batch = sheet.batch;
      var hoSeen = {}, hos = [], b;
      for (b = 0; b < batch.length; b++) {
        var hov = (batch[b].job && batch[b].job.ho) ? String(batch[b].job.ho) : "";
        if (hov && !hoSeen[hov]) { hoSeen[hov] = 1; hos.push(hov); }
      }
      var sizeTxt = L === "4" ? "13.9x9.6 cm，标题 7.2pt/内容 18pt" : "20x13 cm，标题 9.6pt/内容 24pt";
      html += '<div class="s06-review-block">'
        + '<div class="s06-review-meta"><div class="s06-review-ho">' + esc(hos.join(" / ")) + "</div>"
        + '<div class="s06-review-size">' + sizeTxt + "</div></div>"
        + '<div class="s06-review-slot ' + (L === "4" ? "land" : "port") + '"><button type="button" class="s06-review-loupe" title="放大">🔍</button>'
        + emitPage(L, batch) + "</div></div>";
    });
    var box = document.getElementById("s06-review-body");
    box.innerHTML = html;
    document.getElementById("s06-review-mask").className = "s06-review-mask show";
    requestAnimationFrame(function () { fitOverflow(box); });
  }
  function review(rows, opts) {
    opts = opts || {};
    var jobs = packingToJobs(rows);
    var result = validateJobs(jobs);
    if (result.errors.length) {
      return Promise.reject(new Error(result.errors.slice(0, 12).join("\n") + (result.errors.length > 12 ? "\n…" : "")));
    }
    function showPreview() {
      renderReview(jobs);
      return new Promise(function (resolve, reject) {
        document.getElementById("s06-review-back").onclick = function () {
          closeReview();
          reject(new Error("已返回修改"));
        };
        document.getElementById("s06-review-ok").onclick = function () {
          closeReview();
          resolve({ jobs: jobs, ids: jobs.map(function (j) { return j.id; }).filter(Boolean) });
        };
        document.getElementById("s06-review-zoom").onclick = function () {
          var first = document.querySelector("#s06-review-body .s06-review-slot");
          openZoom(first);
        };
        document.getElementById("s06-review-body").onclick = function (ev) {
          var slot = ev.target.closest ? ev.target.closest(".s06-review-slot") : null;
          if (slot) openZoom(slot);
        };
        document.getElementById("s06-zoom-mask").onclick = function (ev) {
          if (ev.target.id === "s06-zoom-mask" || (ev.target.className || "").indexOf("s06-zoom-hint") >= 0) closeZoom();
        };
      });
    }
    if (result.mismatch.length && opts.confirmMismatch) {
      var m = result.mismatch[0];
      var msg = "货号 " + whoOf(m.job) + "：装箱数 × 每箱数量 = " + m.mm.boxes + " × " + m.mm.per + " = " + m.mm.prod + "，签约数 " + m.mm.order + "，不相等。是否进行下一步？";
      return Promise.resolve(opts.confirmMismatch(msg)).then(function (ok) {
        if (!ok) return Promise.reject(new Error("已返回修改"));
        result.mismatch.forEach(function (x) { x.job.qtyMismatchOk = true; });
        return showPreview();
      });
    }
    return showPreview();
  }

  function layoutInfoFromRow(row) {
    var det = detectTemplate((row && (row.country || row.port)) || "");
    var dims = rowDims(row || {});
    var job = { tpl: det ? det.code : "", dims: dims, layout: "auto" };
    var L = layoutOf(job);
    var text = L === "2" ? "一开二 20×13" : L === "4" ? "一开四 13.9×9.6" : "待填尺寸";
    if (det) text += " · " + det.faces + " 面";
    else text += " · 未识别模板";
    return { layout: L, tpl: det ? det.code : "", dest: det ? det.dest : "", faces: det ? det.faces : 0, text: text };
  }

  global.S06Mark = {
    exportPdf: exportPdf,
    packingToJobs: packingToJobs,
    detectTemplate: detectTemplate,
    validateJobs: validateJobs,
    review: review,
    layoutInfoFromRow: layoutInfoFromRow
  };
})(window);
