const COL_W = {
  操作: 88,
  国家: 90,
  发票号: 130,
  跟单员: 96,
  PO: 88,
  HO: 100,
  供应商: 280,
  供应商名称: 220,
  业务员: 96,
  用户名: 88,
  密码: 88,
  显示名: 220,
  创建人: 88,
  创建时间: 150,
  修改人: 88,
  修改时间: 150,
  "Quantity Cartons": 120,
  "Carton size (cm) (LxWxH)": 170,
  CBM: 80,
  Gross: 80,
  Net: 80,
  "Supplier Code": 120,
  "EAN Code": 130,
  CE: 70,
  Description: 260,
  "Quantity (Pieces per Carton)": 160,
  "Total Quantity Pieces": 150,
};
function defaultWidth(label) {
  if (COL_W[label]) return COL_W[label];
  const n = String(label || "").length;
  return Math.min(180, Math.max(72, 14 * n + 24));
}
function applyColgroup(table, widths) {
  let cg = table.querySelector("colgroup");
  if (!cg) {
    cg = document.createElement("colgroup");
    table.insertBefore(cg, table.firstChild);
  }
  cg.innerHTML = widths.map(w => `<col style="width:${w}px">`).join("");
  const total = widths.reduce((a, b) => a + Number(b || 0), 0);
  table.style.width = total + "px";
  table.style.minWidth = total + "px";
  table.style.maxWidth = "none";
}
function enableResize(table, widths, onChange) {
  const ths = table.querySelectorAll("thead th");
  ths.forEach((th, i) => {
    th.querySelectorAll(".col-resizer").forEach(n => n.remove());
    th.style.position = "sticky";
    const z = document.createElement("span");
    z.className = "col-resizer";
    th.appendChild(z);
    z.addEventListener("mousedown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      z.classList.add("active");
      const startX = e.pageX;
      const startW = widths[i];
      const move = (ev) => {
        widths[i] = Math.max(56, Math.min(480, startW + (ev.pageX - startX)));
        applyColgroup(table, widths);
      };
      const up = () => {
        z.classList.remove("active");
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        if (onChange) onChange();
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
  });
}
function enableReorder(table, onDrop) {
  const ths = [...table.querySelectorAll("thead th")];
  ths.forEach((th, i) => {
    if (th.dataset.lock === "1") return;
    th.draggable = true;
    th.addEventListener("dragstart", (e) => {
      if (e.target.classList.contains("col-resizer") || e.target.classList.contains("th-filter")) { e.preventDefault(); return; }
      th.classList.add("dragging");
      e.dataTransfer.setData("text/plain", String(i));
    });
    th.addEventListener("dragend", () => th.classList.remove("dragging"));
    th.addEventListener("dragover", (e) => { e.preventDefault(); th.classList.add("drag-over"); });
    th.addEventListener("dragleave", () => th.classList.remove("drag-over"));
    th.addEventListener("drop", (e) => {
      e.preventDefault();
      th.classList.remove("drag-over");
      const from = Number(e.dataTransfer.getData("text/plain"));
      const to = i;
      if (Number.isNaN(from) || from === to) return;
      if (onDrop) onDrop(from, to);
    });
  });
}
function moveItem(arr, from, to) {
  const next = arr.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}
function pageButtons(cur, total) {
  if (total <= 1) return `<button class="pg on" data-n="1">1</button>`;
  const set = new Set([1, total, cur - 1, cur, cur + 1]);
  if (cur <= 3) [1, 2, 3, 4, 5].forEach(n => set.add(n));
  if (cur >= total - 2) [total - 4, total - 3, total - 2, total - 1, total].forEach(n => set.add(n));
  const nums = [...set].filter(n => n >= 1 && n <= total).sort((a, b) => a - b);
  let html = "";
  for (let i = 0; i < nums.length; i++) {
    if (i && nums[i] - nums[i - 1] > 1) html += `<button class="pg" disabled>…</button>`;
    html += `<button class="pg ${nums[i] === cur ? "on" : ""}" data-n="${nums[i]}">${nums[i]}</button>`;
  }
  return html;
}
