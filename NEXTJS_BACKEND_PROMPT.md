# Prompt 1 — Next.js backend (paste into Claude Code in an empty folder)

Build ONLY the backend of a GATE CSE mock-test app as Next.js (App Router, JavaScript, no TypeScript, no UI yet). Keep it minimal: no extra libraries beyond `next`, `react`, `@supabase/supabase-js`, `@supabase/ssr`, `zod`. All business logic already lives in Postgres RPC functions (deployed); routes only authenticate, validate input, call ONE rpc, map errors.

ENV (.env.local, document in .env.example, never commit): NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY (server only), ADMIN_TOKEN, CRON_SECRET.

FILES
- `lib/supabase.js`: `userClient()` (@supabase/ssr cookie client, also accepts `Authorization: Bearer <jwt>`), `adminClient()` (service role, imported only by admin/cron routes).
- `lib/api.js`: `handle(fn)` wrapper -> try/catch, JSON responses. `rpc(client, name, args)` -> `{data,error}`; throws `ApiError(status, code, detail)`. Error map by Postgres error message: not_authenticated 401; attempt_not_found|paper_not_found|blueprint_not_found|question_not_found 404; attempt_closed|attempt_in_progress|insufficient_questions 409 (detail = parsed JSON); rate_limited 429; bad_request|invalid_response|question_not_in_paper|bad_answer 400; question_not_seen|permission denied 403; else 500 (log, hide internals). Response shape `{ok:true,data}` / `{ok:false,code,detail}`.
- `lib/auth.js`: `requireUser()` (401 if none), `requireAdmin(req)` (header `x-admin-token` === ADMIN_TOKEN).
- Validate bodies/queries with zod; UUID ids.

STUDENT ROUTES (user JWT, RPC args use `p_` prefix of the param names — read `backend/supabase/migrations/002_functions.sql` for exact signatures and match them):
POST /api/papers/generate → create_paper (pass Idempotency-Key header) | GET /api/papers → list_papers | POST /api/papers/[id]/attempts → start_attempt | GET /api/attempts/[id] → get_attempt | PUT /api/attempts/[id]/answers → save_answers (batched, with client_seq) | POST /api/attempts/[id]/submit → submit_attempt | GET /api/attempts/[id]/analysis → get_attempt_analysis | GET /api/me/attempts → list_my_attempts | GET /api/me/progress → get_my_progress | GET /api/papers/[id]/leaderboard → get_leaderboard | GET /api/leaderboard/global → get_global_leaderboard | POST /api/questions/[id]/report → report_question.

ADMIN ROUTES (service role, `requireAdmin`): POST /api/admin/questions → admin_upsert_questions | GET /api/admin/stats → admin_bank_stats | POST /api/admin/questions/[id]/status → admin_set_question_status | POST /api/admin/questions/[id]/regrade → admin_regrade_question.
CRON: GET /api/cron/expire (Authorization: Bearer CRON_SECRET) → expire_overdue_attempts; add `vercel.json` cron every minute.

RULES: never expose service role to the client; never return answer keys during an in-progress attempt (the RPC already guarantees it, don't add fields); `export const dynamic = "force-dynamic"`; no caching of attempt data; CORS not needed (same origin).

DELIVER: the files above + `README.md` (10 lines: env, run, curl example for generate→start→save→submit) + `scripts/smoke.mjs` that signs in with a Supabase test user and exercises generate→start→save→submit→analysis against a running dev server. Then run `next build` and fix errors. Output a short summary, no long explanations.
