import { handle, rpc } from "../../../../../lib/api";
import { requireUser } from "../../../../../lib/auth";
import { uuid } from "../../../../../lib/validation";
export const dynamic = "force-dynamic";
export const GET = handle(async (req, { params }) => { const { client } = await requireUser(req); return rpc(client, "get_attempt_analysis", { p_attempt_id: uuid.parse(params.id) }); });
