"""Capture the exact result of the X write action (TWITTER_CREATION_OF_A_POST)."""
import sys
import traceback

sys.path.insert(0, "/opt/takyon/hermes-agent-main")
from plugins.takyon import composio_distribution  # noqa: E402

text = (
    "Petpal keeps your dog's vet visits, vaccines, and medication "
    "schedules in one place so nothing slips through the cracks."
)
print("text_len:", len(text))
try:
    r = composio_distribution.twitter_execute_tool(
        "TWITTER_CREATION_OF_A_POST",
        arguments={"text": text},
        timeout=120.0,
    )
    print("RESPONSE:", repr(r)[:3000])
except Exception:
    print("RAISED:")
    traceback.print_exc()
