import { z } from "zod";
export const uuid = z.string().uuid();
export const pagination = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(20),
  offset: z.coerce.number().int().min(0).default(0)
});
export const idParam = z.object({ id: uuid });
export const jsonValue = z.any();
