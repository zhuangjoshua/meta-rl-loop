import postgres from "postgres";
import { getDatabaseEnv, getMigrationEnv } from "./env";

type SqlClient = ReturnType<typeof postgres>;

declare global {
  // eslint-disable-next-line no-var
  var polsiaSql: SqlClient | undefined;
  // eslint-disable-next-line no-var
  var polsiaMigrationSql: SqlClient | undefined;
}

export function db() {
  if (!globalThis.polsiaSql) {
    globalThis.polsiaSql = postgres(getDatabaseEnv().DATABASE_URL, {
      max: 6,
      ssl: "require",
      idle_timeout: 20,
      connect_timeout: 10,
      prepare: false
    });
  }
  return globalThis.polsiaSql;
}

export function migrationDb() {
  if (!globalThis.polsiaMigrationSql) {
    globalThis.polsiaMigrationSql = postgres(getMigrationEnv().MIGRATION_DATABASE_URL, {
      max: 1,
      ssl: "require",
      idle_timeout: 20,
      connect_timeout: 10,
      prepare: false
    });
  }
  return globalThis.polsiaMigrationSql;
}

export async function closeDbConnections() {
  await Promise.all([globalThis.polsiaSql?.end(), globalThis.polsiaMigrationSql?.end()]);
  globalThis.polsiaSql = undefined;
  globalThis.polsiaMigrationSql = undefined;
}
