// Served by FastAPI at /app, the API lives on the same origin.
// Opened straight from disk (file://), fall back to the default dev server.
const API_BASE = location.protocol.startsWith("http") ? location.origin : "http://localhost:8000";

// Transaction type codes (sent to the API) -> display labels. Compare codes, never labels.
const TXN_TYPES = {
  sale: { label: "Sale", sign: -1 },
  receipt: { label: "Receipt", sign: +1 },
  adjustment: { label: "Adjustment", sign: null }, // user picks the direction
};

// DSS status codes (from /dss/*) -> display label, badge class, chart bar colour (CSS variable).
// Compare codes, never labels. Key order is the display order of the chips and chart legend
// (matches the backend's sort order).
const DSS_STATUS = {
  CRITICAL: { label: "Critical", cls: "critical", fill: "--status-critical" },
  REORDER: { label: "Reorder", cls: "reorder", fill: "--status-warning" },
  NO_SALES: { label: "No sales", cls: "nosales", fill: "--status-none" },
  OK: { label: "OK", cls: "ok", fill: "--status-good" },
};

// How far back the "units sold" KPI looks. Must equal WINDOW_DAYS in dss.py (same window as /dss demand).
const SALES_WINDOW_DAYS = 7;
