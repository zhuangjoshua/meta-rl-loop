-- Bounded tenant-scoped retention scans for opaque Claude Agent SDK sessions.
--
-- Runtime pruning deletes only whole sessions after taking the same advisory
-- lock used by append/load and rechecking that no entry is newer than cutoff.

begin;

create index if not exists agent_sdk_session_entries_retention_idx
    on public.agent_sdk_session_entries
        (owner_user_id, business_slug, project_key, session_id, created_at desc);

-- The operator worker also sweeps scopes that are never resumed again. Keep
-- its bounded oldest-entry discovery off the load-path index above.
create index if not exists agent_sdk_session_entries_global_retention_idx
    on public.agent_sdk_session_entries
        (created_at, owner_user_id, business_slug, project_key, session_id);

commit;
