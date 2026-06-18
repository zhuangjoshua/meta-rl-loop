#!/usr/bin/env node

// MONEY GATE — DISABLED PAID PATH.
//
// A live call to https://api.lightreel.ai is a BILLABLE provider request charged
// against LIGHTREEL_API_KEY. Per Takyon's hardline rule ("No ungated paid
// capability"), nothing may spend real money without first metering it through a
// money gate: resolve the exact provider cost (unpriced = refuse), reserve before
// the provider call, settle on success, release on failure.
//
// Lightreel discovery is a fixed business-scoped creative/research action, so its
// canonical home is the creative-credit rail
// (hermes-agent-main/plugins/takyon/business_credits.py — reserve→commit→release,
// cost defined in hermes-agent-main/plugins/takyon/core.py
// _CREATIVE_CREDIT_COST_DEFAULTS and the exact provider price in
// hermes-agent-main/agent/usage_pricing.py), brokered server-side by the internal
// creative gateway like the existing /ugc-render, /static-render, and /logo-render
// authority routes in hermes-agent-main/plugins/takyon/creative_gateway.py.
//
// That gate does not exist yet, AND Lightreel has NO resolved per-call price in
// agent/usage_pricing.py, so an unpriced paid action MUST be refused. This script
// therefore NEVER makes the billable request from this ungated skill path: it
// fails closed and records the missing gate. The non-paid steps of the skill
// (build_lightreel_prompt.js, seedancify.js, build_fal_payload.js) still run.
//
// MISSING GATE — what must ship before live discovery is re-enabled:
//   1. core.py: add a `lightreel_discover` action to _CREATIVE_CREDIT_COST_DEFAULTS
//      / _CREATIVE_CREDIT_COST_ENVS (fixed credit price).
//   2. agent/usage_pricing.py: add the exact Lightreel per-request provider cost
//      (e.g. ("lightreel", "discover") with request_cost). Unpriced stays refused.
//   3. creative_gateway.py: add a /internal/creative-gateway/lightreel-render
//      authority route that reserves credits, makes the live call server-side with
//      the Safebox-backed key, commits on success and releases on failure — plus a
//      business_* authority tool that the CEO/skill calls instead of this script.

const fs = require("fs");
const path = require("path");

const GATE_REFUSAL = {
  success: false,
  status: "blocked_missing_money_gate",
  action: "lightreel_discover",
  error:
    "Live Lightreel discovery is a billable provider call with no money gate. " +
    "It is disabled until the creative-credit gate (reserve→commit→release) and " +
    "an exact agent/usage_pricing.py price ship. Unpriced paid actions are refused.",
  missing_gate: {
    rail: "creative-credits (plugins/takyon/business_credits.py)",
    pricing:
      'agent/usage_pricing.py has no resolved ("lightreel", "discover") cost; unpriced = refused',
    credit_action:
      "plugins/takyon/core.py _CREATIVE_CREDIT_COST_DEFAULTS['lightreel_discover'] (not defined)",
    authority_route:
      "plugins/takyon/creative_gateway.py /internal/creative-gateway/lightreel-render (not implemented)",
  },
};

function usage() {
  console.error("Usage: query_lightreel.js <prompt-file> [output-json]");
  process.exit(1);
}

function emitRefusal(outputPath) {
  const serialized = JSON.stringify(GATE_REFUSAL, null, 2);
  if (outputPath) {
    fs.writeFileSync(path.resolve(outputPath), serialized);
  }
  // Surface on stderr so wrappers and operators see the refusal reason, and exit
  // non-zero so no downstream step treats a missing conversation as success.
  console.error(GATE_REFUSAL.error);
  process.exit(2);
}

function main() {
  const promptPath = process.argv[2];
  const outputPath = process.argv[3];

  if (!promptPath) {
    usage();
  }

  // Fail closed: refuse the billable provider call from this ungated path,
  // regardless of whether LIGHTREEL_API_KEY is provisioned. A present key is not
  // authorization to spend — the money gate (reserve/price/settle) is.
  emitRefusal(outputPath);
}

main();
