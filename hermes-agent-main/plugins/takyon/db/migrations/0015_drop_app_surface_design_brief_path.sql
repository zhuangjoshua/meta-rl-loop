-- 0015_drop_app_surface_design_brief_path.sql
-- `design_brief_path` was a seeded planning artifact, not canonical product-surface truth.
-- The surface contract now uses only the normalized source/runtime/customer-shape fields, so
-- existing Postgres schemas should drop the dead column too.

alter table if exists app_surface_contracts
    drop column if exists design_brief_path;
