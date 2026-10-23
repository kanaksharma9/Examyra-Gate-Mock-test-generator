import { handle, rpc } from "../../../../lib/api";
import { requireUser } from "../../../../lib/auth";
export const dynamic = "force-dynamic";
export const GET = handle(async (req) => { const { client } = await requireUser(req); return rpc(client, "get_my_progress"); });
