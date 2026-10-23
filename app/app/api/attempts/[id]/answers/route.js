import { z } from "zod";
import { handle, body, rpc } from "../../../../../lib/api";
import { requireUser } from "../../../../../lib/auth";
import { uuid } from "../../../../../lib/validation";
const schema = z.object({ items: z.array(z.object({ question_id: uuid, response: z.any().nullable().optional(), state: z.string().optional(), time_spent_sec: z.number().int().min(0).optional(), client_seq: z.number().int().min(0).optional(), seq: z.number().int().min(0).optional() })).max(100) });
export const dynamic = "force-dynamic";
export const PUT = handle(async (req, { params }) => { const { client } = await requireUser(req); const v = await body(req, schema); return rpc(client, "save_answers", { p_attempt_id: uuid.parse(params.id), p_items: v.items.map(({ client_seq, ...item }) => ({ ...item, seq: item.seq ?? client_seq ?? 0 })) }); });
