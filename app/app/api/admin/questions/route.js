import { z } from "zod";
import { handle, body, rpc } from "../../../../lib/api";
import { requireAdmin } from "../../../../lib/auth";
const schema = z.object({ rows: z.array(z.record(z.any())).max(500), batch: z.record(z.any()).nullable().optional() });
export const dynamic = "force-dynamic";
export const POST = handle(async (req) => { requireAdmin(req); const v = await body(req, schema); return rpc((await import("../../../../lib/supabase")).adminClient(), "admin_upsert_questions", { p_rows: v.rows, p_batch: v.batch }); });
