const base = process.env.BASE_URL || "http://localhost:3000";
const email = process.env.SMOKE_EMAIL;
const password = process.env.SMOKE_PASSWORD;
if (!email || !password) throw new Error("Set SMOKE_EMAIL and SMOKE_PASSWORD in .env.local or the environment.");
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
if (!supabaseUrl || !anonKey) throw new Error("Set Supabase URL and anon key.");
const signIn = await fetch(`${supabaseUrl}/auth/v1/token?grant_type=password`, {
  method: "POST", headers: { apikey: anonKey, "content-type": "application/json" },
  body: JSON.stringify({ email, password })
});
if (!signIn.ok) throw new Error(`Sign-in failed: ${await signIn.text()}`);
const { access_token: token } = await signIn.json();
const call = async (path, options = {}) => {
  const response = await fetch(`${base}${path}`, { ...options, headers: { Authorization: `Bearer ${token}`, "content-type": "application/json", ...(options.headers || {}) } });
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(`${path}: ${JSON.stringify(data)}`);
  return data.data;
};
const paper = await call("/api/papers/generate", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: "{}" });
const attemptId = await call(`/api/papers/${paper}/attempts`, { method: "POST" });
await call(`/api/attempts/${attemptId}/answers`, { method: "PUT", body: JSON.stringify({ items: [] }) });
await call(`/api/attempts/${attemptId}/submit`, { method: "POST" });
await call(`/api/attempts/${attemptId}/analysis`);
console.log("smoke flow passed");
