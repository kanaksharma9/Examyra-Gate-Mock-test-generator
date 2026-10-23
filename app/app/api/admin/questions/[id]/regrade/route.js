import { z } from "zod";
import { handle, body, rpc } from "../../../../../../lib/api";
import { requireAdmin } from "../../../../../../lib/auth";
import { adminClient } from "../../../../../../lib/supabase";
import { uuid } from "../../../../../../lib/validation";
const schema = z.object({ answer: z.any(), solution_md: z.string().nullable().optional() });
export const dynamic = "force-dynamic";
export const POST = handle(async (req, { params }) => { requireAdmin(req); const v = await body(req, schema); return rpc(adminClient(), "admin_regrade_question", { p_question_id: uuid.parse(params.id), p_answer: v.answer, p_solution_md: v.solution_md }); });
