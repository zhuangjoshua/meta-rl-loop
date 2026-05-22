import { closeDbConnections, db } from "../src/lib/db";

async function main() {
  const sql = db();
  const rows = await sql<{ ok: number }[]>`SELECT 1 AS ok`;
  if (rows[0]?.ok !== 1) throw new Error("database smoke failed");

  const tables = await sql<{ name: string }[]>`
    SELECT table_name AS name
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_name IN (
        'profiles',
        'businesses',
        'business_documents',
        'business_inbox_messages',
        'workflow_jobs',
        'agent_runs',
        'cron_jobs',
        'prompts',
        'platform_rate_limit_buckets',
        'platform_request_logs',
        'campaign_metric_snapshots',
        'customer_response_signals'
      )
    ORDER BY table_name
  `;

  const names = new Set(tables.map((row) => row.name));
  for (const required of [
    "profiles",
    "businesses",
    "business_documents",
    "business_inbox_messages",
    "workflow_jobs",
    "agent_runs",
    "cron_jobs",
    "prompts",
    "platform_rate_limit_buckets",
    "platform_request_logs",
    "campaign_metric_snapshots",
    "customer_response_signals"
  ]) {
    if (!names.has(required)) throw new Error(`missing table ${required}`);
  }

  console.log("smoke: ok");
}

main()
  .catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  })
  .finally(async () => {
    await closeDbConnections();
  });
