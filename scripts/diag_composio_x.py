"""Diagnose the X/Composio EXECUTE broker path (read-only — does NOT post a tweet)."""
import sys
import traceback

sys.path.insert(0, "/opt/takyon/hermes-agent-main")
from plugins.takyon import composio_distribution  # noqa: E402
from plugins.takyon import safebox  # noqa: E402

try:
    print("use_remote_authority:", safebox._use_remote_authority())
except Exception:
    print("use_remote_authority check raised:")
    traceback.print_exc()

print("--- resolve_twitter_connected_account_id ---")
try:
    acct = composio_distribution.resolve_twitter_connected_account_id()
    print("connected_account_id:", acct)
except Exception:
    traceback.print_exc()

print("--- twitter_execute_tool TWITTER_USER_LOOKUP_ME (read-only) ---")
try:
    r = composio_distribution.twitter_execute_tool(
        "TWITTER_USER_LOOKUP_ME",
        arguments={"user_fields": ["username"]},
        timeout=30.0,
    )
    print("LOOKUP_ME OK:", repr(r)[:2000])
except Exception:
    print("LOOKUP_ME FAILED:")
    traceback.print_exc()
