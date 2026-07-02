# Takyon Database Rail

`topology.sql` is the privileged, public-schema-only bootstrap/repair file for the production
migration role topology. It creates the Takyon roles if needed, grants `takyon_migration` the
app-role admin options required by the replayed migration files, and transfers public application
objects to `takyon_migration`.

Normal migrations live in `migrations/` and are replayed in lexical order by `runner.py`. There is no
version table; each SQL file must stay idempotent.

Production migration runs should use:

```bash
takyon migrate
```

The command resolves `TAKYON_MIGRATION_DATABASE_URL`, verifies the connection is using
`takyon_migration`, asserts the topology, replays all migration files, and prints the applied file
list plus a schema fingerprint. Use `takyon migrate --dry-run` to validate the rail and print the
file list without executing the migration files.

Do not use `REASSIGN OWNED BY postgres` on Supabase. Keep ownership repair scoped to `schema public`.
