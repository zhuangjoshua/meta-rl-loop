// Dev stub only. In real products this file is materialized by the platform:
// the platform overwrites the whole _takyon/ directory wholesale at seed/publish
// time with the business's real surface contract and the real runtime client.
// App code must import the kit ONLY through _takyon/ so that swap is invisible.
export const surfaceContext = {
  business: "scaffold-dev",
  runtimeApiBase: "/api/takyon/apps/scaffold-dev",
  frontendApiMode: "prefixed_runtime_api",
  runtimeFeatures: ["auth", "account", "checkout", "generate", "records", "actions"],
  railState: {},
  routes: [],
  plans: [],
};
