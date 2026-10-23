import { z } from "zod";
import { handle, rpc } from "../../../../lib/api";
import { requireUser } from "../../../../lib/auth";
const querySchema = z.object({ limit: z.coerce.number().int().min(1).max(100).default(50) });
export const dynamic = "force-dynamic";
export const GET = handle(async (req) => { const { client } = await requireUser(req); const v = querySchema.parse(Object.fromEntries(new URL(req.url).searchParams)); return rpc(client, "get_global_leaderboard", { p_limit: v.limit }); });
