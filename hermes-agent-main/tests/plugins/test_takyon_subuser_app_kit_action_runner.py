"""Node-gated behavioral tests for the subuser AppKit createActionRunner."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

_KIT = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "takyon"
    / "subuser_app_kit"
    / "runtime-client.js"
)

_HARNESS = """
import { createSubuserRuntimeClient } from "KIT_URL";

const calls = [];
let responder = () => ({ status: 200, body: { success: true } });
const stubFetch = async (url, init) => {
  const body = JSON.parse((init && init.body) || "{}");
  calls.push({ url: String(url), body });
  const next = responder(calls.length);
  if (next.networkError) throw new TypeError("fetch failed");
  return { ok: next.status < 400, status: next.status, json: async () => next.body };
};
globalThis.fetch = stubFetch;

const location = {
  hostname: "localhost",
  href: "http://localhost/app",
  origin: "http://localhost",
  pathname: "/app",
};
const client = createSubuserRuntimeClient({
  runtimeFeatures: ["auth", "account", "actions", "checkout"],
  railState: { actions: "live", checkout: "live" },
  runtimeApiBase: "/api/takyon/apps/testbiz",
  location,
});

const out = {};
const runner = client.createActionRunner("summarize");

responder = () => ({ status: 200, body: { success: true, result: { ok: 1 } } });
const happy = await runner.run({ q: 1 });
// run() returns the action's RESULT unwrapped from the {success,result} transport envelope
// (the contract the browser UI consumes — it reads result fields directly).
out.happy = {
  ok: happy.ok,
  url: calls[0].url,
  key: calls[0].body.idempotency_key,
  state: runner.state(),
};

let resolveHold;
globalThis.fetch = () =>
  new Promise((resolve) => {
    resolveHold = () =>
      resolve({ ok: true, status: 200, json: async () => ({ success: true }) });
  });
const slow = runner.run({});
out.pendingState = runner.state();
try {
  await runner.run({});
  out.pendingGuard = "no-throw";
} catch (error) {
  out.pendingGuard = error.kind;
}
resolveHold();
await slow;
globalThis.fetch = stubFetch;

responder = () => ({ status: 402, body: { success: false, error: "app budget exceeded" } });
try {
  await runner.run({});
} catch (error) {
  out.budget = {
    kind: error.kind,
    status: error.status,
    hasCheckout:
      typeof error.checkoutUrl === "string" && error.checkoutUrl.includes("checkout=upgrade"),
  };
}

responder = () => ({ status: 429, body: { success: false, error: "action_already_running" } });
try {
  await runner.run({});
} catch (error) {
  out.busy = error.kind;
}
responder = () => ({
  status: 429,
  body: { success: false, error: "Too many action requests. Try again shortly." },
});
try {
  await runner.run({});
} catch (error) {
  out.rate = error.kind;
}

calls.length = 0;
responder = (n) => (n === 1 ? { networkError: true } : { status: 200, body: { success: true } });
try {
  await runner.run({});
} catch (error) {
  out.networkKind = error.kind;
}
await runner.run({});
out.keyReusedAfterNetworkFailure =
  calls[0].body.idempotency_key === calls[1].body.idempotency_key;

calls.length = 0;
responder = (n) =>
  n === 1
    ? { status: 402, body: { success: false, error: "app budget exceeded" } }
    : { status: 200, body: { success: true } };
try {
  await runner.run({});
} catch (error) {
  // server outcome: key must not be replayed
}
await runner.run({});
out.keyRegeneratedAfterServerOutcome =
  calls[0].body.idempotency_key !== calls[1].body.idempotency_key;

responder = () => ({
  status: 400,
  body: { success: false, error: "action exceeded the 60s deadline" },
});
try {
  await runner.run({});
} catch (error) {
  out.timeout = error.kind;
}

const offClient = createSubuserRuntimeClient({ runtimeFeatures: ["auth"], location });
try {
  await offClient.createActionRunner("x").run({});
} catch (error) {
  out.unavailable = error.kind;
}

console.log(JSON.stringify(out));
"""


def test_create_action_runner_behavior(tmp_path):
    script = tmp_path / "runner_test.mjs"
    script.write_text(_HARNESS.replace("KIT_URL", _KIT.as_uri()), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    assert out["happy"]["ok"] == 1
    assert out["happy"]["url"].endswith("/actions/summarize")
    assert out["happy"]["key"].startswith("action:summarize:")
    assert out["happy"]["state"] == "idle"

    assert out["pendingState"] == "pending"
    assert out["pendingGuard"] == "already_running"

    assert out["budget"] == {"kind": "budget", "status": 402, "hasCheckout": True}
    assert out["busy"] == "already_running"
    assert out["rate"] == "rate_limited"

    assert out["networkKind"] == "network"
    assert out["keyReusedAfterNetworkFailure"] is True
    assert out["keyRegeneratedAfterServerOutcome"] is True

    assert out["timeout"] == "timeout"
    assert out["unavailable"] == "unavailable"
