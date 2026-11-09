// The only place that talks to the backend. Every failure becomes an ApiError
// whose message is safe to show the user as-is.

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status; // 0 = network failure
  }
}

async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, `Backend not reachable at ${API_BASE}. Is uvicorn running?`);
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, errorMessage(data, res.status));
  return data;
}

// 404/409 send {"detail": "<message>"}; 422 sends {"detail": [{loc, msg, ...}, ...]}.
function errorMessage(data, status) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => {
        const field = (e.loc || []).filter((p) => p !== "body").join(".");
        const msg = String(e.msg).replace(/^Value error, /, ""); // Pydantic's prefix for custom validators
        return field ? `${field}: ${msg}` : msg;
      })
      .join("; ");
  }
  return `Request failed (HTTP ${status})`;
}
