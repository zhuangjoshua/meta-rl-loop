-- 0080_stripe_dispute_state.sql
-- Durable current/terminal state closes withdrawal-vs-reinstatement races per Stripe dispute.

begin;

create table if not exists stripe_dispute_states (
    stripe_dispute_id text primary key check (stripe_dispute_id ~ '^du_[A-Za-z0-9]+$'),
    status text not null,
    terminal boolean not null default false,
    provider_event_id text,
    provider_event_created bigint not null default 0 check (provider_event_created >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

revoke all on table stripe_dispute_states from public;
revoke all on table stripe_dispute_states from
    takyon_operator_runtime, takyon_app_runtime, takyon_runtime, takyon_app, safebox;
grant select, insert, update on table stripe_dispute_states to takyon_safebox_authority;

commit;
