let paperKey;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export async function api(path, options = {}) {
  let last;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const headers = { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) };
      if (path === "/api/papers/generate") {
        paperKey ||= crypto.randomUUID();
        headers["Idempotency-Key"] = paperKey;
      }
      const response = await fetch(path, { ...options, headers });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) throw Object.assign(new Error(payload.code || "request_failed"), { status: response.status, code: payload.code, detail: payload.detail });
      return payload.data;
    } catch (error) {
      last = error;
      if (error.status || attempt === 2) throw error;
      await sleep(300 * 2 ** attempt);
    }
  }
  throw last;
}
export const get = (path) => api(path);
export const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
export const put = (path, body) => api(path, { method: "PUT", body: JSON.stringify(body) });
