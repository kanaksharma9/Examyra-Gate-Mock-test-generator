import { handle, rpc } from "../../../../../lib/api";
import { requireUser } from "../../../../../lib/auth";
import { pagination, uuid } from "../../../../../lib/validation";
export const dynamic = "force-dynamic";
export const GET = handle(async (req, { params }) => { const { client } = await requireUser(req); const q = Object.fromEntries(new URL(req.url).searchParams); const v = pagination.parse(q); return rpc(client, "get_leaderboard", { p_paper_id: uuid.parse(params.id), p_limit: v.limit, p_offset: v.offset }); });
