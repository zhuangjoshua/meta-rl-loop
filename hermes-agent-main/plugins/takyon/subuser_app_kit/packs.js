function uniq(values) {
  const seen = new Set();
  const out = [];
  for (const value of values || []) {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    out.push(text);
  }
  return out;
}

const DEFAULT_SUBSCRIPTION_STYLE = "monthly";

export const APP_MODE_PACKS = {
  standard_saas: {
    label: "Standard SaaS",
    recommendedRoutes: ["/", "/app"],
    recommendedSections: ["hero", "proof", "pricing", "faq", "app_shell"],
    requiredRails: ["auth", "account"],
    optionalRails: ["checkout", "usage"],
  },
  ai_tool: {
    label: "AI Tool",
    recommendedRoutes: ["/", "/app", "/app/workspace"],
    recommendedSections: ["hero", "workflow", "pricing", "app_shell", "results"],
    requiredRails: ["auth", "account", "generate"],
    optionalRails: ["checkout", "usage"],
  },
  api_product: {
    label: "API Product",
    recommendedRoutes: ["/", "/docs", "/app"],
    recommendedSections: ["hero", "quickstart", "docs", "pricing", "dashboard"],
    requiredRails: ["auth", "account"],
    optionalRails: ["checkout", "usage", "generate"],
  },
};

export const SUBSCRIPTION_PACKS = {
  monthly: {
    label: "Monthly",
    recommendedModules: ["pricing", "upgrade_cta", "account_panel"],
    requiredRails: ["auth", "account", "checkout"],
  },
};

export const API_MODE_PACKS = {
  none: {
    label: "No API Surface",
    recommendedModules: [],
    requiredRails: [],
  },
  docs_playground: {
    label: "Docs + Playground",
    recommendedModules: ["docs_page", "api_quickstart", "playground"],
    requiredRails: ["auth", "account"],
    optionalRails: ["generate", "usage"],
  },
  external_api: {
    label: "External API",
    recommendedModules: ["docs_page", "api_quickstart", "key_console", "usage_pill"],
    requiredRails: ["auth", "account"],
    notes: [
      "Requires a real customer-facing key issuance and rotation rail before this can be presented as fully live.",
    ],
  },
};

export function planSubuserSurface(context = {}) {
  const appMode = APP_MODE_PACKS[context.appMode] || {};
  const subscriptionStyle = context.subscriptionStyle || DEFAULT_SUBSCRIPTION_STYLE;
  const subscription = SUBSCRIPTION_PACKS[subscriptionStyle] || SUBSCRIPTION_PACKS[DEFAULT_SUBSCRIPTION_STYLE] || {};
  const apiMode = API_MODE_PACKS[context.apiMode] || {};
  return {
    appMode: context.appMode || "",
    subscriptionStyle,
    apiMode: context.apiMode || "",
    recommendedRoutes: uniq([
      ...(appMode.recommendedRoutes || []),
      ...(context.routes || []),
    ]),
    recommendedSections: uniq([
      ...(appMode.recommendedSections || []),
    ]),
    recommendedModules: uniq([
      ...(subscription.recommendedModules || []),
      ...(apiMode.recommendedModules || []),
    ]),
    requiredRails: uniq([
      ...(appMode.requiredRails || []),
      ...(subscription.requiredRails || []),
      ...(apiMode.requiredRails || []),
    ]),
    optionalRails: uniq([
      ...(appMode.optionalRails || []),
      ...(apiMode.optionalRails || []),
    ]),
    notes: uniq([
      ...((subscription.notes || [])),
      ...((apiMode.notes || [])),
    ]),
  };
}
