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

function encodeRoutePart(value) {
  return encodeURIComponent(String(value || "").trim());
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

function randomKeySuffix() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replaceAll("-", "");
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}

function classifyActionError(error, { checkoutCallable = false, location } = {}) {
  const status = Number(error && error.status) || 0;
  const message = String((error && error.message) || "action failed");
  let kind = "action_error";
  if (error && error.rail) {
    kind = "unavailable";
  } else if (status === 402) {
    kind = "budget";
  } else if (status === 429) {
    kind = message.includes("action_already_running") ? "already_running" : "rate_limited";
  } else if (status === 404) {
    kind = "unavailable";
  } else if (/deadline|timed?[ _-]?out/i.test(message)) {
    kind = "timeout";
  } else if (!status) {
    kind = "network";
  }
  const classified = new Error(message);
  classified.kind = kind;
  if (status) classified.status = status;
  classified.cause = error;
  if (kind === "budget" && checkoutCallable && location) {
    classified.checkoutUrl = defaultCheckoutUrl("upgrade", location);
  }
  return classified;
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
    async cancelSubscription(payload = {}) {
      ensureRail("account");
      return jsonRequest(routeUrl("account"), {
        method: "POST",
        body: JSON.stringify({
          ...payload,
          action: "cancel_subscription",
        }),
      });
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
    async listDirectory(options = {}) {
      ensureRail("directory");
      const params = new URLSearchParams();
      if (options.limit != null && options.limit !== "") {
        params.set("limit", String(options.limit));
      }
      const suffix = params.toString();
      return jsonRequest(`${routeUrl("directory")}${suffix ? `?${suffix}` : ""}`, {
        method: "GET",
      });
    },
    async getDirectoryMe() {
      ensureRail("directory");
      return jsonRequest(routeUrl("directory/me"), { method: "GET" });
    },
    async getDirectoryEntry(appUserId) {
      ensureRail("directory");
      return jsonRequest(
        routeUrl(`directory/${encodeRoutePart(appUserId)}`),
        { method: "GET" },
      );
    },
    async updateDirectoryMe(payload = {}) {
      ensureRail("directory");
      return jsonRequest(routeUrl("directory/me"), {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    async disableDirectoryMe() {
      ensureRail("directory");
      return jsonRequest(routeUrl("directory/me"), { method: "DELETE" });
    },
    async listRecords(options = {}) {
      ensureRail("records");
      const recordType = String(options.record_type || options.type || "").trim();
      // records-v2: a bounded server-side query when filters/sort/cursor are present;
      // otherwise the plain newest-first GET list.
      if (options.filters || options.sort || options.cursor) {
        return jsonRequest(routeUrl("records/query"), {
          method: "POST",
          body: JSON.stringify({
            record_type: recordType || undefined,
            filters: options.filters || [],
            sort: options.sort || undefined,
            cursor: options.cursor || undefined,
            limit: options.limit != null && options.limit !== "" ? options.limit : undefined,
          }),
        });
      }
      const params = new URLSearchParams();
      if (recordType) params.set("type", recordType);
      if (options.limit != null && options.limit !== "") {
        params.set("limit", String(options.limit));
      }
      const suffix = params.toString();
      return jsonRequest(`${routeUrl("records")}${suffix ? `?${suffix}` : ""}`, {
        method: "GET",
      });
    },
    async getRecord(type, id) {
      ensureRail("records");
      return jsonRequest(
        routeUrl(`records/${encodeRoutePart(type)}/${encodeRoutePart(id)}`),
        { method: "GET" },
      );
    },
    async saveRecord(payload = {}) {
      ensureRail("records");
      const recordType = String(payload.record_type || payload.type || "").trim();
      if (!recordType) {
        throw new Error("record_type is required");
      }
      const recordId = String(payload.record_id || payload.id || "").trim();
      const route = recordId
        ? `records/${encodeRoutePart(recordType)}/${encodeRoutePart(recordId)}`
        : "records";
      return jsonRequest(routeUrl(route), {
        method: "POST",
        body: JSON.stringify({
          ...payload,
          record_type: recordType,
          record_id: recordId || undefined,
        }),
      });
    },
    async deleteRecord(type, id) {
      ensureRail("records");
      return jsonRequest(
        routeUrl(`records/${encodeRoutePart(type)}/${encodeRoutePart(id)}`),
        { method: "DELETE" },
      );
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
    async uploadMedia(file) {
      ensureRail("media");
      if (!file) {
        throw new Error("a file is required");
      }
      const form = new FormData();
      form.append("file", file);
      // Do NOT set Content-Type — the browser sets the multipart boundary itself.
      const response = await fetch(routeUrl("media"), {
        method: "POST",
        credentials: "same-origin",
        body: form,
      });
      const payload = await response
        .json()
        .catch(() => ({ success: false, error: `non_json_response:${response.status}` }));
      if (!response.ok) {
        const error = new Error(String(payload.error || `media_upload_failed:${response.status}`));
        error.status = response.status;
        error.payload = payload;
        throw error;
      }
      return payload;
    },
    mediaUrl(id) {
      ensureRail("media");
      return routeUrl(`media/${encodeRoutePart(id)}`);
    },
    async deleteMedia(id) {
      ensureRail("media");
      return jsonRequest(routeUrl(`media/${encodeRoutePart(id)}`), { method: "DELETE" });
    },
    async listConnections(options = {}) {
      ensureRail("connections");
      const params = new URLSearchParams();
      const state = String(options.state || "").trim();
      if (state) params.set("state", state);
      if (options.limit != null && options.limit !== "") {
        params.set("limit", String(options.limit));
      }
      const suffix = params.toString();
      return jsonRequest(`${routeUrl("connections")}${suffix ? `?${suffix}` : ""}`, {
        method: "GET",
      });
    },
    async actOnConnection(payload = {}) {
      ensureRail("connections");
      return jsonRequest(routeUrl("connections"), {
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
    async invokeAction(name, payload = {}, options = {}) {
      ensureRail("actions");
      const actionName = String(name || "").trim();
      if (!actionName) {
        throw new Error("action name is required");
      }
      return jsonRequest(routeUrl(`actions/${encodeRoutePart(actionName)}`), {
        method: "POST",
        body: JSON.stringify({
          payload,
          idempotency_key:
            options.idempotency_key ||
            options.idempotencyKey ||
            undefined,
        }),
      });
    },
    createActionRunner(name) {
      const actionName = String(name || "").trim();
      if (!actionName) {
        throw new Error("action name is required");
      }
      let pending = false;
      let replayKey = "";
      return {
        action: actionName,
        state() {
          return pending ? "pending" : "idle";
        },
        async run(payload = {}, options = {}) {
          if (pending) {
            const busy = new Error(`action ${actionName} is already running`);
            busy.kind = "already_running";
            throw busy;
          }
          pending = true;
          const idempotencyKey =
            options.idempotency_key ||
            options.idempotencyKey ||
            replayKey ||
            `action:${actionName}:${randomKeySuffix()}`;
          try {
            ensureRail("actions");
            const result = await jsonRequest(
              routeUrl(`actions/${encodeRoutePart(actionName)}`),
              {
                method: "POST",
                body: JSON.stringify({ payload, idempotency_key: idempotencyKey }),
              },
            );
            replayKey = "";
            return result;
          } catch (error) {
            const classified = classifyActionError(error, {
              checkoutCallable: ALLOW_CALL_STATES.has(railStateFor("checkout")),
              location,
            });
            // Replay the same key only when the request may never have reached the
            // runtime; reusing it after a server outcome would double-charge.
            replayKey = classified.kind === "network" ? idempotencyKey : "";
            throw classified;
          } finally {
            pending = false;
          }
        },
      };
    },
    usageFromAccount(accountPayload = {}) {
      return accountPayload && isObject(accountPayload.usage_this_period)
        ? accountPayload.usage_this_period
        : null;
    },
  };
}

export { DEFAULT_FRONTEND_API_MODE };
