import { z } from "zod";
import { handle, body, rpc } from "../../../../lib/api";
import { requireUser } from "../../../../lib/auth";
const schema = z.object({ blueprint_id: z.number().int().positive().nullable().optional(), visibility: z.enum(["public", "private"]).default("private"), title: z.string().max(200).nullable().optional() });
export const dynamic = "force-dynamic";
export const POST = handle(async (req) => { const { client } = await requireUser(req); const v = await body(req, schema); return rpc(client, "create_paper", { p_blueprint_id: v.blueprint_id, p_source: "generated", p_visibility: v.visibility, p_idempotency_key: req.headers.get("idempotency-key"), p_title: v.title }); });
