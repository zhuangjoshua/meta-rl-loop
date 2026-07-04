-- 0071_events_read_indexes.sql
-- Fix the Supabase Disk-IO-budget depletion (provider alert 2026-07-04).
--
-- Measured on prod: `events` had ONLY its primary key while every hot read path filters and
-- sorts it — 615,219 sequential scans reading 12.36 BILLION cumulative rows (vs 398 index
-- scans), each scan walking ~20k rows and re-sorting by created_at (280 GB of cumulative
-- temp-file spill, the actual disk-IO burner; the site stayed fast only because the table
-- fits in cache). The table is append-only and growing, so this got worse superlinearly.
--
-- The three indexes match the exact live query shapes in core.py / cli.py:
--   1. business_slug = ? AND event_type = ?        ORDER BY created_at DESC LIMIT n
--      (pulse snapshot reads, RL memory fetch, ceo_turn chat reads — the dominant shape)
--   2. business_slug = ? [AND event_type != / LIKE 'dashboard.run.%'] ORDER BY created_at DESC
--      (dashboard logs poller + mixed-type recent-events reads)
--   3. event_type = ?                              ORDER BY created_at DESC LIMIT n
--      (cross-business shared-learnings fetch on wakes)
-- created_at is TEXT holding ISO-8601 UTC, so lexical btree order == chronological order —
-- identical semantics to the existing ORDER BY.
--
-- Additive-only; no grants, no RLS, no behavior change. Write amplification on this
-- insert-only table is three small btree maintenances per event — negligible next to the
-- read savings.

begin;

create index if not exists events_business_type_created_idx
    on events (business_slug, event_type, created_at desc);

create index if not exists events_business_created_idx
    on events (business_slug, created_at desc);

create index if not exists events_type_created_idx
    on events (event_type, created_at desc);

commit;
