import { z } from "zod";
import { handle, body, rpc } from "../../../../../../lib/api";
import { requireAdmin } from "../../../../../../lib/auth";
import { adminClient } from "../../../../../../lib/supabase";
import { uuid } from "../../../../../../lib/validation";
const schema = z.object({ ids: z.array(uuid).min(1).max(500).optional(), status: z.enum(["pending", "approved", "rejected", "retired"]) });
export const dynamic = "force-dynamic";
export const POST = handle(async (req, { params }) => { requireAdmin(req); const v = await body(req, schema); return rpc(adminClient(), "admin_set_question_status", { p_ids: v.ids || [uuid.parse(params.id)], p_status: v.status }); });
