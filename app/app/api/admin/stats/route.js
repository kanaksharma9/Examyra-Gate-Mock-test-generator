import { handle, rpc } from "../../../../lib/api";
import { requireAdmin } from "../../../../lib/auth";
import { adminClient } from "../../../../lib/supabase";
export const dynamic = "force-dynamic";
export const GET = handle(async (req) => { requireAdmin(req); const value = new URL(req.url).searchParams.get("target"); const target = value ? Number(value) : 200; if (!Number.isInteger(target) || target < 1 || target > 10000) { const { ApiError } = await import("../../../../lib/api"); throw new ApiError(400, "bad_request"); } return rpc(adminClient(), "admin_bank_stats", { p_target: target }); });
