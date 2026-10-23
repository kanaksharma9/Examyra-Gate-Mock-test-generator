import { z } from "zod";
import { handle, body, rpc } from "../../../../../lib/api";
import { requireUser } from "../../../../../lib/auth";
import { uuid } from "../../../../../lib/validation";
const schema = z.object({ reason: z.string().min(1).max(100), note: z.string().max(1000).nullable().optional() });
export const dynamic = "force-dynamic";
export const POST = handle(async (req, { params }) => { const { client } = await requireUser(req); const v = await body(req, schema); return rpc(client, "report_question", { p_question_id: uuid.parse(params.id), p_reason: v.reason, p_note: v.note }); });
