const DEFAULT_FRONTEND_API_MODE = "same_origin_product_host_with_prefixed_fallback";
const ALLOW_CALL_STATES = new Set(["live", "declared"]);

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function normalizeArray(values) {
  if (!Array.isArray(values)) return [];
  const seen = new Set();
  const out = [];
  for (const value of values) {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    out.push(text);
  }
  return out;
}

function normalizeRailState(raw) {
  if (!isObject(raw)) return {};
  const out = {};
  for (const [key, value] of Object.entries(raw)) {
    const rail = String(key || "").trim();
    const rawState = String(value || "").trim().toLowerCase();
    const state =
      rawState === "unverified" || rawState === "unknown" ? "declared" : rawState;
    if (!rail || !state) continue;
    out[rail] = state;
  }
  return out;
}

function normalizeRoute(route) {
  return String(route || "").replace(/^\/+/, "");
}

function joinRoute(base, route) {
  const cleanRoute = normalizeRoute(route);
  const cleanBase = String(base || "").trim().replace(/\/+$/, "");
  return cleanBase ? `${cleanBase}/${cleanRoute}` : `/${cleanRoute}`;
}

function defaultLocation() {
  if (typeof window !== "undefined" && window.location) return window.location;
  return { hostname: "", href: "", pathname: "/", origin: "" };
}

function looksLikeProductHost(hostname) {
  const host = String(hostname || "").trim().toLowerCase();
  if (!host) return false;
  if (host === "localhost" || host === "127.0.0.1" || host === "::1") return false;
  if (host.startsWith("app.")) return false;
  return host.endsWith(".fourmanifold.com");
}

export function resolveSubuserRuntimeBase(config = {}) {
  const frontendApiMode = String(
    config.frontendApiMode || DEFAULT_FRONTEND_API_MODE,
  ).trim();
  const runtimeApiBase = String(config.runtimeApiBase || "").trim();
  const location = config.location || defaultLocation();
  const preferSameOrigin =
    typeof config.preferSameOrigin === "boolean"
      ? config.preferSameOrigin
      : frontendApiMode === DEFAULT_FRONTEND_API_MODE &&
        looksLikeProductHost(location.hostname);
  if (preferSameOrigin) return "";
  return runtimeApiBase.replace(/\/+$/, "");
}

async function jsonRequest(url, init = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
  const payload = await response
    .json()
    .catch(() => ({ success: false, error: `non_json_response:${response.status}` }));
  if (!response.ok) {
    const error = new Error(
      String(payload.error || payload.detail || `request_failed:${response.status}`),
    );
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function railUnavailableError(rail, state) {
  const error = new Error(`rail_unavailable:${rail}:${state || "undeclared"}`);
  error.rail = rail;
  error.railState = state || "undeclared";
  return error;
}

function defaultCheckoutUrl(kind, location) {
  const current = new URL(location.href || location.origin || "http://localhost/");
  current.searchParams.set("checkout", kind);
  return current.toString();
}

export function createSubuserRuntimeClient(context = {}) {
  const runtimeFeatures = normalizeArray(context.runtimeFeatures);
  const railState = normalizeRailState(context.railState);
  const frontendApiMode = String(
    context.frontendApiMode || DEFAULT_FRONTEND_API_MODE,
  ).trim();
  const runtimeApiBase = String(context.runtimeApiBase || "").trim();
  const location = context.location || defaultLocation();

  function railStateFor(rail) {
    if (railState[rail]) return railState[rail];
    if (runtimeFeatures.includes(rail)) return "declared";
    return "undeclared";
  }

  function ensureRail(rail) {
    const state = railStateFor(rail);
    if (!ALLOW_CALL_STATES.has(state)) {
      throw railUnavailableError(rail, state);
    }
    return state;
  }

  function routeUrl(route) {
    const base = resolveSubuserRuntimeBase({
      runtimeApiBase,
      frontendApiMode,
      location,
    });
    return joinRoute(base, route);
  }

  return {
    context: {
      ...context,
      frontendApiMode,
      runtimeApiBase,
      runtimeFeatures,
      railState,
    },
    routeUrl,
    railStateFor,
    isRailCallable(rail) {
      return ALLOW_CALL_STATES.has(railStateFor(rail));
    },
    buildVerifyUrl({ token, redirect } = {}) {
      const url = new URL(routeUrl("auth/verify"), location.origin || "http://localhost");
      if (token) url.searchParams.set("token", token);
      if (redirect) url.searchParams.set("redirect", redirect);
      return url.toString();
    },
    async requestAuth(payload = {}) {
      ensureRail("auth");
      return jsonRequest(routeUrl("auth/request"), {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    async session() {
      ensureRail("auth");
      return jsonRequest(routeUrl("session"), { method: "GET" });
    },
    async account() {
      ensureRail("account");
      return jsonRequest(routeUrl("account"), { method: "GET" });
    },
    async profile() {
      ensureRail("profile");
      return jsonRequest(routeUrl("profile"), { method: "GET" });
    },
    async updateProfile(payload = {}) {
      ensureRail("profile");
      return jsonRequest(routeUrl("profile"), {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    async checkout(payload = {}) {
      ensureRail("checkout");
      return jsonRequest(routeUrl("checkout"), {
        method: "POST",
        body: JSON.stringify({
          ...payload,
          success_url:
            payload.success_url ||
            payload.successUrl ||
            defaultCheckoutUrl("success", location),
          cancel_url:
            payload.cancel_url ||
            payload.cancelUrl ||
            defaultCheckoutUrl("cancel", location),
        }),
      });
    },
    async recordUsage(payload = {}) {
      ensureRail("usage");
      return jsonRequest(routeUrl("usage"), {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    async generate(payload = {}) {
      ensureRail("generate");
      return jsonRequest(routeUrl("generate"), {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    usageFromAccount(accountPayload = {}) {
      return accountPayload && isObject(accountPayload.usage_this_period)
        ? accountPayload.usage_this_period
        : null;
    },
  };
}

export { DEFAULT_FRONTEND_API_MODE };
