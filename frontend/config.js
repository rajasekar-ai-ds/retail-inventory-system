// Served by FastAPI at /app, the API lives on the same origin.
// Opened straight from disk (file://), fall back to the default dev server.
const API_BASE = location.protocol.startsWith("http") ? location.origin : "http://localhost:8000";

// Transaction type codes (sent to the API) -> display labels. Compare codes, never labels.
const TXN_TYPES = {
  sale: { label: "Sale", sign: -1 },
  receipt: { label: "Receipt", sign: +1 },
  adjustment: { label: "Adjustment", sign: null }, // user picks the direction
};

// How far back the "units sold" KPI looks.
const SALES_WINDOW_DAYS = 7;
