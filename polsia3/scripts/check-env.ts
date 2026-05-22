import {
  getAppEnv,
  getAuth0Env,
  getCronEnv,
  getDatabaseEnv,
  getLlmsEnv,
  getPostmarkEnv,
  getStripeEnv,
  getVercelEnv,
  getXClientEnv
} from "../src/lib/env";

const checks = [
  ["app", getAppEnv],
  ["database", getDatabaseEnv],
  ["auth0", getAuth0Env],
  ["cron", getCronEnv],
  ["llms", getLlmsEnv],
  ["vercel", getVercelEnv],
  ["stripe", getStripeEnv],
  ["postmark", getPostmarkEnv],
  ["x-client", getXClientEnv]
] as const;

for (const [name, check] of checks) {
  check();
  console.log(`${name}: ok`);
}

if (process.env.X_PLATFORM_ACCESS_TOKEN || process.env.X_PLATFORM_REFRESH_TOKEN || process.env.X_PLATFORM_USERNAME) {
  throw new Error("Stale X platform env tokens are present. Runtime X tokens must come from the encrypted DB integration row.");
}

console.log("stale-x-env: absent");
