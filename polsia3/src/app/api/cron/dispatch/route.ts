import { dispatchDueCronJobs } from "@/lib/cron-jobs";
import { getCronEnv } from "@/lib/env";
import { UnauthorizedError } from "@/lib/errors";
import { jsonError, jsonOk } from "@/lib/http";

function requireCronAuth(request: Request) {
  const expected = `Bearer ${getCronEnv().CRON_SECRET}`;
  const actual = request.headers.get("authorization") || "";
  if (actual !== expected) throw new UnauthorizedError("Invalid cron authorization.");
}

export async function GET(request: Request) {
  try {
    requireCronAuth(request);
    const result = await dispatchDueCronJobs({ dispatcherId: "vercel-cron", limit: 5 });
    return jsonOk({ ok: true, ...result });
  } catch (error) {
    return jsonError(error);
  }
}
