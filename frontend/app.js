// ---------- State ----------

const state = { suppliers: [], products: [] };
let stockChart = null;

const $ = (id) => document.getElementById(id);

// ---------- Formatting ----------

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
const rupees = (paise) => "₹" + (paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const toPaise = (rupeeText) => Math.round(parseFloat(rupeeText) * 100);
const fmtInt = (n) => n.toLocaleString("en-IN");
const fmtDate = (iso) => new Date(iso).toLocaleString();

const supplierById = (id) => state.suppliers.find((s) => s.supplier_id === id);
const productById = (id) => state.products.find((p) => p.product_id === id);

// Show/hide an inline message element inside a form or section.
function showMsg(el, text) {
  el.textContent = text || "";
  el.hidden = !text;
}
const formMsg = (form, role) => form.querySelector(`[data-role="${role}"]`);

// ---------- Loading ----------

async function loadAll() {
  try {
    const since = Date.now() - SALES_WINDOW_DAYS * 86400000;
    const [suppliers, products, txns, alerts, summary] = await Promise.all([
      api("/suppliers"),
      api("/products"),
      api("/transactions?limit=1000"),
      api("/dss/alerts"),
      api("/dss/summary"),
    ]);
    state.suppliers = suppliers;
    state.products = products;
    const soldUnits = txns
      .filter((t) => t.txn_type === "sale" && Date.parse(t.txn_date) >= since)
      .reduce((sum, t) => sum - t.qty, 0); // sale qty is negative
    setConnection(true);
    showMsg($("global-error"), "");
    renderKpis(soldUnits);
    renderDss(alerts, summary);
    renderChart();
    renderProducts();
    renderSuppliers();
    renderSelects();
    await loadHistory();
  } catch (err) {
    setConnection(false);
    showMsg($("global-error"), err.message);
  }
}

async function loadHistory() {
  const productId = $("history-filter").value;
  const query = new URLSearchParams({ limit: "50" });
  if (productId) query.set("product_id", productId);
  const txns = await api(`/transactions?${query}`);
  $("history-body").innerHTML = txns.length
    ? txns
        .map((t) => `<tr>
          <td>${t.txn_id}</td>
          <td>${esc(fmtDate(t.txn_date))}</td>
          <td>${esc(productById(t.product_id)?.name ?? `#${t.product_id}`)}</td>
          <td>${esc(TXN_TYPES[t.txn_type]?.label ?? t.txn_type)}</td>
          <td class="num">${t.qty > 0 ? "+" : ""}${fmtInt(t.qty)}</td>
        </tr>`)
        .join("")
    : `<tr><td colspan="5" class="muted">No transactions yet.</td></tr>`;
}

function setConnection(ok) {
  const el = $("conn");
  el.textContent = ok ? `Connected · ${API_BASE}` : "Backend offline";
  el.className = "conn " + (ok ? "ok" : "down");
}

// ---------- Rendering ----------

function renderKpis(soldUnits) {
  const units = state.products.reduce((s, p) => s + p.current_stock, 0);
  const value = state.products.reduce((s, p) => s + p.current_stock * p.unit_price, 0);
  const out = state.products.filter((p) => p.current_stock === 0).length;
  $("kpi-products").textContent = fmtInt(state.products.length);
  $("kpi-units").textContent = fmtInt(units);
  $("kpi-value").textContent = rupees(value);
  $("kpi-out").textContent = fmtInt(out);
  $("kpi-sold-label").textContent = `Units sold (last ${SALES_WINDOW_DAYS} days)`;
  $("kpi-sold").textContent = fmtInt(soldUnits);
}

const statusBadge = (code) => {
  const s = DSS_STATUS[code] ?? { label: code, cls: "nosales" }; // unknown code: show it raw, in grey
  return `<span class="badge ${s.cls}">${esc(s.label)}</span>`;
};

// Alerts arrive already sorted by the backend (CRITICAL, REORDER, NO_SALES, OK); keep that order.
function renderDss(alerts, summary) {
  $("dss-summary").innerHTML = Object.keys(DSS_STATUS)
    .map((code) => `<span class="chip">${statusBadge(code)} ${fmtInt(summary[code] ?? 0)}</span>`)
    .join("");
  $("dss-body").innerHTML = alerts.length
    ? alerts
        .map((a) => `<tr>
          <td>${esc(a.name)}</td>
          <td>${statusBadge(a.status)}</td>
          <td class="num">${fmtInt(a.stock)}</td>
          <td class="num">${a.lead_time_days}</td>
          <td class="num">${a.avg_daily_demand.toFixed(1)}</td>
          <td class="num">${a.days_of_cover === null ? "–" : a.days_of_cover.toFixed(1)}</td>
          <td class="num">${a.reorder_point.toFixed(1)}</td>
          <td class="num">${a.recommended_order_qty ? fmtInt(a.recommended_order_qty) : "–"}</td>
          <td class="reason">${esc(a.reason)}</td>
        </tr>`)
        .join("")
    : `<tr><td colspan="9" class="muted">No products yet.</td></tr>`;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function renderChart() {
  if (typeof Chart === "undefined") {
    $("chart-box").innerHTML = `<p class="muted">Chart unavailable (Chart.js CDN not reachable). See the Products table below.</p>`;
    return;
  }
  const rows = [...state.products].sort((a, b) => a.current_stock - b.current_stock);
  $("chart-box").style.height = Math.max(160, rows.length * 30 + 40) + "px";

  const textColor = cssVar("--text-2");
  const gridColor = cssVar("--grid");
  const data = {
    labels: rows.map((p) => p.name),
    datasets: [{
      label: "Units in stock",
      data: rows.map((p) => p.current_stock),
      backgroundColor: cssVar("--series-1"),
      borderRadius: 4,
      borderSkipped: "start", // round only the data end, keep the baseline square
      maxBarThickness: 18,
    }],
  };
  const options = {
    indexAxis: "y",
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: {
      legend: { display: false }, // one series: the card title names it
      tooltip: { callbacks: { label: (ctx) => ` ${fmtInt(ctx.parsed.x)} units` } },
    },
    scales: {
      x: { beginAtZero: true, ticks: { color: textColor, precision: 0 }, grid: { color: gridColor }, border: { display: false } },
      y: { ticks: { color: textColor }, grid: { display: false }, border: { color: gridColor } },
    },
  };
  if (stockChart) stockChart.destroy();
  stockChart = new Chart($("stock-chart"), { type: "bar", data, options });
}

function renderProducts() {
  $("products-body").innerHTML = state.products.length
    ? state.products
        .map((p) => {
          const s = supplierById(p.supplier_id);
          const badge = p.current_stock === 0 ? `<span class="badge critical">Out of stock</span>` : "";
          return `<tr>
            <td>${esc(p.name)}</td>
            <td>${esc(s?.name ?? `#${p.supplier_id}`)}</td>
            <td class="num">${s ? s.lead_time_days : "–"}</td>
            <td class="num">${rupees(p.unit_price)}</td>
            <td class="num">${fmtInt(p.current_stock)}${badge}</td>
            <td class="actions-cell">
              <button class="link" data-edit-product="${p.product_id}">Edit</button>
              <button class="link danger" data-delete-product="${p.product_id}">Delete</button>
            </td>
          </tr>`;
        })
        .join("")
    : `<tr><td colspan="6" class="muted">No products yet. Add one below.</td></tr>`;
}

function renderSuppliers() {
  $("suppliers-body").innerHTML = state.suppliers.length
    ? state.suppliers
        .map((s) => `<tr>
          <td>${esc(s.name)}</td>
          <td>${esc(s.phone ?? "–")}</td>
          <td class="num">${s.lead_time_days} d</td>
          <td class="actions-cell">
            <button class="link" data-edit-supplier="${s.supplier_id}">Edit</button>
            <button class="link danger" data-delete-supplier="${s.supplier_id}">Delete</button>
          </td>
        </tr>`)
        .join("")
    : `<tr><td colspan="4" class="muted">No suppliers yet.</td></tr>`;
}

// Refill every product/supplier dropdown, keeping the current selection where possible.
function renderSelects() {
  const productOpts = state.products.map((p) => `<option value="${p.product_id}">${esc(p.name)}</option>`).join("");
  const supplierOpts = state.suppliers.map((s) => `<option value="${s.supplier_id}">${esc(s.name)}</option>`).join("");
  fillSelect($("txn-form").product_id, productOpts);
  fillSelect($("product-form").supplier_id, supplierOpts);
  fillSelect($("history-filter"), `<option value="">All products</option>` + productOpts);
}
function fillSelect(select, html) {
  const keep = select.value;
  select.innerHTML = html;
  if ([...select.options].some((o) => o.value === keep)) select.value = keep;
}

// ---------- Transaction form ----------

function setupTxnForm() {
  const form = $("txn-form");
  form.txn_type.innerHTML = Object.entries(TXN_TYPES)
    .map(([code, t]) => `<option value="${code}">${esc(t.label)}</option>`)
    .join("");
  form.txn_type.addEventListener("change", () => {
    $("direction-field").hidden = TXN_TYPES[form.txn_type.value].sign !== null;
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showMsg(formMsg(form, "error"), "");
    showMsg(formMsg(form, "success"), "");
    const type = TXN_TYPES[form.txn_type.value];
    const sign = type.sign ?? Number(form.direction.value);
    const body = {
      product_id: Number(form.product_id.value),
      txn_type: form.txn_type.value,
      qty: sign * Number(form.qty.value),
    };
    try {
      const result = await api("/transactions", { method: "POST", body });
      const name = productById(result.product_id)?.name ?? `#${result.product_id}`;
      showMsg(formMsg(form, "success"), `Recorded. ${name} stock is now ${fmtInt(result.current_stock)}.`);
      form.qty.value = "";
      await loadAll();
    } catch (err) {
      showMsg(formMsg(form, "error"), err.message); // 409 insufficient stock lands here
    }
  });
}

// ---------- Product & supplier forms (add / edit share one form each) ----------

function setupEditableForm({ form, title, noun, toBody, path }) {
  const cancel = formMsg(form, "cancel");
  const reset = () => {
    form.reset();
    form.edit_id.value = "";
    title.textContent = `Add ${noun}`;
    cancel.hidden = true;
    showMsg(formMsg(form, "error"), "");
  };
  cancel.addEventListener("click", reset);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showMsg(formMsg(form, "error"), "");
    const id = form.edit_id.value;
    try {
      await api(id ? `${path}/${id}` : path, { method: id ? "PUT" : "POST", body: toBody(form) });
      reset();
      await loadAll();
    } catch (err) {
      showMsg(formMsg(form, "error"), err.message);
    }
  });

  // Returns a function that puts the form into edit mode with the given values.
  return (id, values) => {
    reset();
    form.edit_id.value = id;
    for (const [k, v] of Object.entries(values)) form[k].value = v ?? "";
    title.textContent = `Edit ${noun}`;
    cancel.hidden = false;
    form.scrollIntoView({ behavior: "smooth", block: "center" });
  };
}

function setupForms() {
  const editProduct = setupEditableForm({
    form: $("product-form"),
    title: $("product-form-title"),
    noun: "product",
    path: "/products",
    toBody: (f) => ({ name: f.name.value, supplier_id: Number(f.supplier_id.value), unit_price: toPaise(f.unit_price.value) }),
  });
  const editSupplier = setupEditableForm({
    form: $("supplier-form"),
    title: $("supplier-form-title"),
    noun: "supplier",
    path: "/suppliers",
    toBody: (f) => ({ name: f.name.value, phone: f.phone.value.trim() || null, lead_time_days: Number(f.lead_time_days.value) }),
  });

  // One click handler for all Edit/Delete buttons in the tables (they're re-rendered often).
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    const d = b.dataset;
    if (d.editProduct) {
      const p = productById(Number(d.editProduct));
      editProduct(p.product_id, { name: p.name, supplier_id: p.supplier_id, unit_price: (p.unit_price / 100).toFixed(2) });
    } else if (d.editSupplier) {
      const s = supplierById(Number(d.editSupplier));
      editSupplier(s.supplier_id, { name: s.name, phone: s.phone, lead_time_days: s.lead_time_days });
    } else if (d.deleteProduct) {
      await confirmDelete(`/products/${d.deleteProduct}`, productById(Number(d.deleteProduct))?.name, $("products-error"));
    } else if (d.deleteSupplier) {
      await confirmDelete(`/suppliers/${d.deleteSupplier}`, supplierById(Number(d.deleteSupplier))?.name, formMsg($("supplier-form"), "error"));
    }
  });
}

async function confirmDelete(path, name, errorEl) {
  if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;
  showMsg(errorEl, "");
  try {
    await api(path, { method: "DELETE" });
    await loadAll();
  } catch (err) {
    showMsg(errorEl, err.message); // 409: still has products / has transactions
  }
}

// ---------- Start ----------

setupTxnForm();
setupForms();
$("history-filter").addEventListener("change", () => loadHistory().catch((err) => showMsg($("global-error"), err.message)));
// Re-colour the chart when the OS switches light/dark.
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderChart);
loadAll();
