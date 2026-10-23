import { ApiError } from "./api";
import { userClient } from "./supabase";

export async function requireUser(req) {
  const client = userClient(req);
  const { data, error } = await client.auth.getUser();
  if (error || !data.user) throw new ApiError(401, "not_authenticated");
  return { client, user: data.user };
}

export function requireAdmin(req) {
  if (!process.env.ADMIN_TOKEN || req.headers.get("x-admin-token") !== process.env.ADMIN_TOKEN) {
    throw new ApiError(401, "not_authenticated");
  }
}

export function requireCron(req) {
  if (!process.env.CRON_SECRET || req.headers.get("authorization") !== `Bearer ${process.env.CRON_SECRET}`) {
    throw new ApiError(401, "not_authenticated");
  }
}
