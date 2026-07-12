-- Canonical server-owned record locators. A ref is unguessable and immutable, but never authority:
-- every lookup remains constrained by business_slug + app_user_id (and RLS).
alter table app_records
    add column if not exists record_ref text;

update app_records
set record_ref = 'tkr_' || replace(gen_random_uuid()::text, '-', '')
where record_ref is null;

alter table app_records
    alter column record_ref set default ('tkr_' || replace(gen_random_uuid()::text, '-', '')),
    alter column record_ref set not null;

create unique index if not exists app_records_record_ref_idx
    on app_records (record_ref);

alter table app_records
    drop constraint if exists app_records_record_ref_shape,
    add constraint app_records_record_ref_shape
        check (record_ref ~ '^tkr_[0-9a-f]{32}$');
