-- Supabase Auth is now the sole product sub-user IdP. The live runtime no longer
-- exposes /auth/request or /auth/verify, and session minting happens through
-- business_supabase_login -> app_identity.start_session.
drop table if exists app_magic_links;
