import { handle, rpc } from "../../../../../lib/api";
import { requireUser } from "../../../../../lib/auth";
import { uuid } from "../../../../../lib/validation";
export const dynamic = "force-dynamic";
export const POST = handle(async (req, { params }) => { const { client } = await requireUser(req); const id = uuid.parse(params.id); return rpc(client, "start_attempt", { p_paper_id: id }); });
