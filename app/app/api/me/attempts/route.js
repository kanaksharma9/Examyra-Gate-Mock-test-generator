import { handle, rpc } from "../../../../lib/api";
import { requireUser } from "../../../../lib/auth";
import { pagination } from "../../../../lib/validation";
export const dynamic = "force-dynamic";
export const GET = handle(async (req) => { const { client } = await requireUser(req); const q = Object.fromEntries(new URL(req.url).searchParams); const v = pagination.parse(q); return rpc(client, "list_my_attempts", { p_limit: v.limit, p_offset: v.offset }); });
