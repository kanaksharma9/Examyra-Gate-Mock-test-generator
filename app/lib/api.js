export class ApiError extends Error {
  constructor(status, code, detail) {
    super(code);
    this.status = status; this.code = code; this.detail = detail;
  }
}

function mapped(message, detail) {
  const code = String(message || "").split("\n")[0].trim();
  if (code === "not_authenticated") return new ApiError(401, code);
  if (["attempt_not_found", "paper_not_found", "blueprint_not_found", "question_not_found"].includes(code)) return new ApiError(404, code);
  if (["attempt_closed", "attempt_in_progress", "insufficient_questions"].includes(code)) {
    let parsed = detail;
    try { parsed = JSON.parse(detail); } catch {}
    return new ApiError(409, code, parsed);
  }
  if (code === "rate_limited") return new ApiError(429, code, detail);
  if (["bad_request", "invalid_response", "question_not_in_paper", "bad_answer"].includes(code)) return new ApiError(400, code, detail);
  if (code === "question_not_seen" || code.toLowerCase().includes("permission denied")) return new ApiError(403, code);
  return new ApiError(500, "internal_error");
}

export async function rpc(client, name, args = {}) {
  const { data, error } = await client.rpc(name, args);
  if (error) {
    const mappedError = mapped(error.message, error.details);
    if (mappedError.status === 500) console.error(`RPC ${name} failed`, error);
    throw mappedError;
  }
  return data;
}

export function jsonOk(data, status = 200) { return Response.json({ ok: true, data }, { status }); }
export function handle(fn) {
  return async (...args) => {
    try { return jsonOk(await fn(...args)); }
    catch (error) {
      if (error instanceof ApiError) return Response.json({ ok: false, code: error.code, detail: error.detail }, { status: error.status });
      if (error?.name === "ZodError") return Response.json({ ok: false, code: "bad_request", detail: error.flatten() }, { status: 400 });
      console.error(error);
      return Response.json({ ok: false, code: "internal_error" }, { status: 500 });
    }
  };
}

export async function body(req, schema) {
  const result = schema.safeParse(await req.json());
  if (!result.success) throw new ApiError(400, "bad_request", result.error.flatten());
  return result.data;
}
