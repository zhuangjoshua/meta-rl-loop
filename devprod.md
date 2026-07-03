# Dev vs Prod

Same code runs both. `TAKYON_ENV` flips which world the runtime talks to: unset/`prod` → production;
`dev` → the dev twin (its **own** Supabase, fake-money Stripe — break anything, prod never notices).

## Reach each (same named commands in both)

```bash
# PROD — operate real businesses (SSH-tunnels to the prod control plane)
scripts/takyon-operator-prod.sh sai            # console 4, as sai
scripts/takyon-operator-prod.sh josh           # console 4, as josh
scripts/takyon-operator-prod.sh console 4 --user sai --shells 4

# DEV TWIN — same names, isolated dev database (starts the dev Safebox for you)
scripts/takyon-operator-dev.sh sai             # dev shell, as sai
scripts/takyon-operator-dev.sh josh            # dev shell, as josh
scripts/takyon-operator-dev.sh seed <name>...  # one-time: create those users in the twin
```

Names (`sai`/`josh`) live in one place — `scripts/operator-users.sh` — shared by both rails, so a
name means the same person in dev and prod. Add a teammate with one line there or in
`scripts/operator-users.conf` (`<name> <uuid>`).

There's also `scripts/takyon-local-dev.sh shell` — a *fully-on-laptop* sandbox (local Safebox, local
DB, single-owner). Use it for quick throwaway work; use the **dev twin** (above) when you want a
prod-shaped environment with real per-user isolation.

## Current differences

| | **prod** | **dev twin** |
|---|---|---|
| Database | four-manifold-**prod** Supabase | four-manifold-**dev** Supabase — separate project, never touch each other |
| Contents | real businesses | near-empty sandbox |
| Stripe | (the live plane) | **TEST** mode — fake cards |
| Safebox | prod Safebox `67.205.158.170` | its own dev Safebox (started by the dev rail) |
| Servers | prod VPS hosts | its own DO droplets (the load balancer + 2-replica split live here first) |
| Isolation | `owner_user_id` fence + RLS | **same** fence, same migrations — proven: as sai a business is invisible to josh |

## Isolation — can someone claim my work?

**Different user-id per person → fully isolated, in both dev and prod.** Every business has
`owner_user_id NOT NULL`; the operator shell only ever lists `where owner_user_id = <you>`, so `sai`
literally cannot see or touch `josh`'s businesses. Verified in the dev twin, not assumed.

Within the *same* user-id, running work (bootstrap/wake/iterate jobs) is claimed by workers; UC1
(ClaimScope) reserves each session's in-flight jobs to that session's pool, spilling to another
worker only if yours dies — so nothing is silently taken while you're alive, and nothing is stranded
if you drop.

## Promote a change dev → prod

There's no "mirror" — the code is the same on `main`; you just move it up the deploy rail:

1. **Test in dev:** `scripts/takyon-operator-dev.sh <you>` (real providers, Stripe TEST, dev DB).
2. **Push:** `git push origin main`.
3. **Deploy to prod:** the AGENTS.md rail — `rsync` `hermes-agent-main` to both VPS hosts →
   `takyon migrate` **if** you added files under `plugins/takyon/db/migrations/` → restart services →
   verify `is-active`.

Migrations are additive/nullable by convention, so `migrate` is safe to run before the code restart
and is a no-op when nothing changed.
