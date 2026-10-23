import { handle, rpc } from "../../../../lib/api";
import { requireCron } from "../../../../lib/auth";
import { adminClient } from "../../../../lib/supabase";
export const dynamic = "force-dynamic";
export const GET = handle(async (req) => { requireCron(req); const value = new URL(req.url).searchParams.get("limit"); const limit = value ? Number(value) : 500; if (!Number.isInteger(limit) || limit < 1 || limit > 5000) { const { ApiError } = await import("../../../../lib/api"); throw new ApiError(400, "bad_request"); } return rpc(adminClient(), "expire_overdue_attempts", { p_limit: limit }); });
