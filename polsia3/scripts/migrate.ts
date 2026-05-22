import fs from "node:fs/promises";
import path from "node:path";
import { closeDbConnections, migrationDb } from "../src/lib/db";
import { seedRequiredPrompts } from "../src/lib/prompts";

async function main() {
  const sql = migrationDb();
  const migrationsDir = path.join(process.cwd(), "db", "migrations");
  const names = (await fs.readdir(migrationsDir)).filter((name) => name.endsWith(".sql")).sort();

  await sql`
    CREATE TABLE IF NOT EXISTS _migrations (
      name text PRIMARY KEY,
      applied_at timestamptz NOT NULL DEFAULT now()
    )
  `;

  for (const name of names) {
    const seen = await sql<{ exists: boolean }[]>`
      SELECT EXISTS(SELECT 1 FROM _migrations WHERE name = ${name}) AS exists
    `;
    if (seen[0].exists) {
      console.log(`skip ${name}`);
      continue;
    }

    const body = await fs.readFile(path.join(migrationsDir, name), "utf8");
    await sql.begin(async (tx) => {
      await tx.unsafe(body);
      await tx`
        INSERT INTO _migrations (name)
        VALUES (${name})
      `;
    });
    console.log(`applied ${name}`);
  }

  await seedRequiredPrompts();
  console.log("seeded prompts");
}

main()
  .catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  })
  .finally(async () => {
    await closeDbConnections();
  });
