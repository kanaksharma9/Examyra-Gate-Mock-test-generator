import { z } from "zod";
import { handle, rpc } from "../../../lib/api";
import { requireUser } from "../../../lib/auth";
import { pagination } from "../../../lib/validation";
const querySchema = pagination.extend({ scope: z.enum(["public", "mine", "all"]).default("public") });
export const dynamic = "force-dynamic";
export const GET = handle(async (req) => { const { client } = await requireUser(req); const v = querySchema.parse(Object.fromEntries(new URL(req.url).searchParams)); return rpc(client, "list_papers", { p_scope: v.scope, p_limit: v.limit, p_offset: v.offset }); });
