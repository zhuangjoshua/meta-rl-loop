-- 0055_app_runtime_rate_limit_port.sql
--
-- Product-app AI/search calls validate the app session in Python, then count the
-- request in the shared api_rate_limits fixed-window table before any provider
-- or money-gated work. After the split-role cutover, takyon_app_runtime no
-- longer inherits the old control-plane grants, so the rate-limit preflight
-- failed before it could reach the Safebox usage gate.
--
-- api_rate_limits is an abuse counter, not a money/access ledger. Grant app
-- runtime roles the narrow DML needed for the atomic upsert and returning read.
-- Keep DELETE unavailable; pruning remains an operator/Safebox maintenance job.

grant select, insert, update on api_rate_limits
    to takyon_app_runtime, takyon_app;

drop policy if exists takyon_app_runtime_rate_limit_counter on api_rate_limits;
create policy takyon_app_runtime_rate_limit_counter
    on api_rate_limits
    for all
    to takyon_app_runtime, takyon_app
    using (true)
    with check (true);

revoke delete on api_rate_limits
    from takyon_app_runtime, takyon_app;
