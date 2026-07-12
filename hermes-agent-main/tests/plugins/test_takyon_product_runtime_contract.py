"""Behavioral conformance for the small product runtime contract."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from plugins.takyon.app_runtime_constants import product_runtime_contract


_ROOT = Path(__file__).resolve().parents[2]
_KIT = _ROOT / "plugins" / "takyon" / "subuser_app_kit"
_SCAFFOLD = _KIT / "scaffold"
_VITE = _SCAFFOLD / "node_modules" / "vite" / "dist" / "node" / "index.js"


def _node(source: str, *, cwd: Path) -> dict:
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", source],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_product_runtime_contract_returns_isolated_typed_truth():
    first = product_runtime_contract()
    first["subscription"]["cancellation"]["effective_timing"] = "changed"  # type: ignore[typeddict-item]
    first["records"]["identifier"] = "raw_id"  # type: ignore[typeddict-item]

    assert product_runtime_contract() == {
        "version": 1,
        "subscription": {
            "cancellation": {
                "version": 1,
                "effective_timing": "immediate",
                "refund_policy": "none",
            }
        },
        "records": {"identifier": "opaque_ref"},
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_web_client_accepts_only_exact_immediate_no_refund_cancellation_result():
    valid = product_runtime_contract()
    valid_result = {
        "recorded": True,
        "stripe_subscription_status": "canceled",
        "cancel_at_period_end": False,
        "effective_immediately": True,
        "subscription_cancellation_policy": valid["subscription"]["cancellation"],
        "product_runtime_contract": valid,
    }
    source = f"""
import {{ createSubuserRuntimeClient }} from {_KIT.joinpath('runtime-client.js').as_uri()!r};

const calls = [];
const responses = {json.dumps([
    valid_result,
    {**valid_result, "cancel_at_period_end": True, "effective_immediately": False},
    {
        **valid_result,
        "subscription_cancellation_policy": {
            "version": 1,
            "effective_timing": "immediate",
            "refund_policy": "optional",
        },
    },
    {key: value for key, value in valid_result.items() if key != "subscription_cancellation_policy"},
    {"authenticated": True, "product_runtime_contract": valid},
    {"authenticated": True},
])};
globalThis.fetch = async (url, init = {{}}) => {{
  calls.push({{ url: String(url), method: init.method, body: init.body }});
  const payload = responses.shift();
  return {{ ok: true, status: 200, json: async () => payload }};
}};
const client = createSubuserRuntimeClient({{
  runtimeApiBase: "/api/takyon/apps/notewave",
  runtimeFeatures: ["account"],
  railState: {{ account: "live" }},
  location: {{ href: "https://notewave.coscale.app/app", origin: "https://notewave.coscale.app" }},
}});
const accepted = await client.cancelSubscription();
const failures = [];
for (let index = 0; index < 3; index += 1) {{
  try {{
    await client.cancelSubscription();
    failures.push("accepted-invalid-result");
  }} catch (error) {{
    failures.push(String(error && error.message));
  }}
}}
const account = await client.account();
let invalidAccount = "";
try {{
  await client.account();
}} catch (error) {{
  invalidAccount = String(error && error.message);
}}
console.log(JSON.stringify({{
  acceptedImmediate: accepted.effective_immediately,
  failures,
  requestBodies: calls.filter((call) => call.method === "POST").map((call) => JSON.parse(call.body)),
  accountContractVersion: account.product_runtime_contract.version,
  invalidAccount,
  refundMethod: typeof client.refundSubscription,
}}));
"""

    result = _node(source, cwd=_ROOT)

    assert result == {
        "acceptedImmediate": True,
        "failures": ["invalid_subscription_cancellation_result"] * 3,
        "requestBodies": [{"action": "cancel_subscription"}] * 4,
        "accountContractVersion": 1,
        "invalidAccount": "invalid_product_runtime_contract",
        "refundMethod": "undefined",
    }


@pytest.mark.skipif(
    shutil.which("node") is None or not _VITE.is_file(),
    reason="node and scaffold dependencies are required",
)
def test_appkit_keeps_canceled_distinct_from_past_due():
    source = f"""
import {{ createServer }} from {_VITE.as_uri()!r};
const server = await createServer({{
  root: {str(_SCAFFOLD)!r},
  logLevel: "silent",
  server: {{ middlewareMode: true }},
  appType: "custom",
}});
try {{
  const hooks = await server.ssrLoadModule("/src/lib/hooks.ts");
  const canceledAccount = {{
    subscription_cancellation_policy: {{ version: 1, effective_timing: "immediate", refund_policy: "none" }},
    entitlements: [{{
      source: "stripe",
      status: "cancelled",
      tier: "paid",
      stripe_subscription_id: "sub_done",
    }}],
  }};
  const pastDueAccount = {{
    subscription_cancellation_policy: {{ version: 1, effective_timing: "immediate", refund_policy: "none" }},
    entitlements: [{{
      source: "stripe",
      status: "past_due",
      tier: "paid",
      stripe_subscription_id: "sub_due",
    }}],
  }};
  const canceledStatus = hooks.subscriptionStateFromAccount(canceledAccount);
  const pastDueStatus = hooks.subscriptionStateFromAccount(pastDueAccount);
  const canceledState = hooks.viewerAccessStateForSubscription(false, canceledStatus);
  const pastDueState = hooks.viewerAccessStateForSubscription(false, pastDueStatus);
  console.log(JSON.stringify({{
    canceledStatus,
    pastDueStatus,
    canceledState,
    pastDueState,
    canceledCta: hooks.resolveViewerCta({{
      authenticated: true,
      entitled: false,
      state: canceledState,
      subscriptionState: canceledStatus,
    }}).primaryLabel,
    pastDueCta: hooks.resolveViewerCta({{
      authenticated: true,
      entitled: false,
      state: pastDueState,
      subscriptionState: pastDueStatus,
    }}).primaryLabel,
    canceledStillCancelable: hooks.hasNonterminalStripeSubscription(canceledAccount),
    pastDueStillCancelable: hooks.hasNonterminalStripeSubscription(pastDueAccount),
  }}));
}} finally {{
  await server.close();
}}
"""

    assert _node(source, cwd=_SCAFFOLD) == {
        "canceledStatus": "canceled",
        "pastDueStatus": "past_due",
        "canceledState": "canceled",
        "pastDueState": "past_due",
        "canceledCta": "Subscribe again",
        "pastDueCta": "Update billing",
        "canceledStillCancelable": False,
        "pastDueStillCancelable": True,
    }
