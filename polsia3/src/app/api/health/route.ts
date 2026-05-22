import { db } from "@/lib/db";
import { getAppEnv, getCronEnv, getDatabaseEnv, getVercelEnv } from "@/lib/env";
import { jsonError, jsonOk } from "@/lib/http";

export async function GET() {
  try {
    getAppEnv();
    getDatabaseEnv();
    getCronEnv();
    getVercelEnv();
    const sql = db();
    await sql`SELECT 1`;
    return jsonOk({ ok: true, database: "ready", env: "ready" });
  } catch (error) {
    return jsonError(error);
  }
}
