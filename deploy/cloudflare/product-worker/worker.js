/**
 * Takyon product-site edge worker.
 *
 * Serves the BUILT product static (`<slug>.fourmanifold.com`) from the R2 bucket
 * `product-sites` at the Cloudflare edge, and forwards EVERYTHING else — every
 * `/api/*` request and every reserved operator host — to the real origin
 * (operator Caddy at 137.184.75.57) byte-for-byte unchanged.
 *
 * Security invariants (do not weaken):
 *  - The `/api/*` passthrough is the auth/paywall/usage/entitlements rail. It must
 *    forward the original method, headers and body verbatim, return the origin's
 *    response verbatim, and NEVER be cached, rewritten, or short-circuited here.
 *  - R2 only ever holds public built dist (`<slug>/<build_id>/<rel>` + the pointer
 *    `<slug>/current`). The worker can only read keys under `<slug>/<build_id>/`,
 *    and the path is sanitised so a request can never escape that prefix.
 *  - The origin stays locked to Cloudflare IPs (the `fourmanifold_edge_only` Caddy
 *    snippet). A Worker subrequest egresses from Cloudflare IPs, so it passes that
 *    gate; direct non-CF clients still get 403. See README for the DNS contract.
 */

// Reserved hosts that are NOT product sites. These belong to the operator
// control plane / dashboard and must always go straight to the origin.
// Keep in sync with the `not host ...` lists in the two Caddyfiles.
const RESERVED_HOSTS = new Set([
  "app.fourmanifold.com",
  "skills.fourmanifold.com",
  "www.fourmanifold.com",
  "admin.fourmanifold.com",
  "dashboard.fourmanifold.com",
  "research-composer.fourmanifold.com",
]);

// Product slug grammar — mirrors `_safe_product_slug` in
// hermes-agent-main/takyon_cli/web_server.py:8414 (single char, or
// [a-z0-9] bookended around up to 78 inner [a-z0-9-]).
const SLUG_RE = /^[a-z0-9]$|^[a-z0-9][a-z0-9-]{0,78}[a-z0-9]$/;

// Long-immutable cache for content-hashed assets (Vite emits hashed names under
// /assets/). Short cache for HTML so a live-build pointer flip is picked up fast.
const IMMUTABLE_CACHE = "public, max-age=31536000, immutable";
const HTML_CACHE = "public, max-age=60";
const ASSET_CACHE = "public, max-age=86400"; // stable-named, non-hashed static

const CONTENT_TYPES = {
  html: "text/html; charset=utf-8",
  htm: "text/html; charset=utf-8",
  js: "text/javascript; charset=utf-8",
  mjs: "text/javascript; charset=utf-8",
  css: "text/css; charset=utf-8",
  json: "application/json; charset=utf-8",
  map: "application/json; charset=utf-8",
  txt: "text/plain; charset=utf-8",
  xml: "application/xml; charset=utf-8",
  svg: "image/svg+xml",
  ico: "image/x-icon",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  avif: "image/avif",
  woff: "font/woff",
  woff2: "font/woff2",
  ttf: "font/ttf",
  otf: "font/otf",
  eot: "application/vnd.ms-fontobject",
  pdf: "application/pdf",
  wasm: "application/wasm",
  mp4: "video/mp4",
  webm: "video/webm",
  mp3: "audio/mpeg",
  wav: "audio/wav",
};

export default {
  /**
   * @param {Request} request
   * @param {{ PRODUCT_SITES: R2Bucket, ORIGIN_HOST: string }} env
   */
  async fetch(request, env) {
    const url = new URL(request.url);
    const host = url.hostname.toLowerCase();

    // ── Passthrough rail ────────────────────────────────────────────────
    // Reserved operator hosts, OR any /api/* path on any host, go to the real
    // origin unchanged. This is the security boundary: auth, sessions, actions,
    // generate, checkout, webhooks, /api/pty, /api/events all live under /api/*
    // (see web_server.py:3021 `/api/takyon/apps/{business}/{route:path}`).
    if (RESERVED_HOSTS.has(host) || url.pathname.startsWith("/api/")) {
      return forwardToOrigin(request, env);
    }

    // ── Static rail (R2) ────────────────────────────────────────────────
    // Only GET/HEAD make sense for static. Anything else on a product host that
    // is not /api/* is not a thing this site serves — send it to the origin so
    // behaviour is identical to today (origin returns its own 404/405).
    if (request.method !== "GET" && request.method !== "HEAD") {
      return forwardToOrigin(request, env);
    }

    const slug = host.split(".")[0];
    if (!SLUG_RE.test(slug)) {
      // Not a valid product slug (and not reserved) — let the origin answer,
      // exactly as Caddy's wildcard block would have.
      return forwardToOrigin(request, env);
    }

    return serveStatic(slug, url, request, env);
  },
};

/**
 * Forward a request to the real origin (operator Caddy) unchanged.
 *
 * The Worker route covers `*.fourmanifold.com/*`, so `fetch(request)` against the
 * same hostname would re-enter this Worker (infinite loop). We pin the subrequest
 * to a grey-clouded origin hostname (`env.ORIGIN_HOST`, e.g. origin.fourmanifold.com
 * -> 137.184.75.57, DNS-only / not proxied, NOT on the Worker route) via
 * `cf.resolveOverride`, while keeping the ORIGINAL Host header so Caddy still
 * matches the per-business site block and serves the right cert/SNI.
 *
 * Because the subrequest still egresses from Cloudflare's network, it arrives at
 * Caddy from a Cloudflare IP and passes `fourmanifold_edge_only`. Direct clients
 * hitting origin.fourmanifold.com are NOT on the CF IP allowlist and still get 403.
 */
function forwardToOrigin(request, env) {
  const originHost = env.ORIGIN_HOST; // e.g. "origin.fourmanifold.com"
  if (!originHost) {
    // Misconfiguration: never silently bypass auth by serving anything else.
    return new Response("origin not configured", { status: 503 });
  }

  // Preserve method, headers and body verbatim. We do NOT touch the Host header:
  // Caddy keys its product site block + origin cert on Host=<slug>.fourmanifold.com.
  const subreq = new Request(request, {
    cf: {
      // Send the connection to the grey-clouded origin record instead of looping
      // back through this Worker route, without rewriting the URL/Host the origin
      // sees. resolveOverride only changes where the TCP connection goes.
      resolveOverride: originHost,
      // The static rail owns caching; the dynamic origin response must never be
      // cached at the edge — auth/paywall/usage responses are per-request.
      cacheEverything: false,
    },
    redirect: "manual",
  });

  return fetch(subreq);
}

/**
 * Serve a product static file from R2 under `<slug>/<build_id>/...`.
 */
async function serveStatic(slug, url, request, env) {
  // Resolve the live build pointer: `<slug>/current` contains the build_id.
  const pointer = await env.PRODUCT_SITES.get(`${slug}/current`);
  if (!pointer) {
    return notFound(slug, "no live build pointer");
  }
  const buildId = (await pointer.text()).trim().toLowerCase();
  // Build ids are lowercase hex (storage.build_object_prefix:
  // re.fullmatch(r"[0-9a-f]{16,64}")). Validate before using in a key.
  if (!/^[0-9a-f]{16,64}$/.test(buildId)) {
    return notFound(slug, "invalid build pointer");
  }

  const prefix = `${slug}/${buildId}/`;

  // Sanitise the request path into a relative key that is ALWAYS under `prefix`.
  const rel = sanitizeRel(url.pathname);
  if (rel === null) {
    return notFound(slug, "unsafe path");
  }

  const wantHead = request.method === "HEAD";

  // 1) Exact file match.
  let key = prefix + (rel === "" ? "index.html" : rel);
  let object = await getObject(env, key, request);
  if (object) {
    return objectResponse(object, key, wantHead);
  }

  // 2) Directory-style request (`/foo/`) -> `/foo/index.html`.
  if (rel.endsWith("/")) {
    key = prefix + rel + "index.html";
    object = await getObject(env, key, request);
    if (object) {
      return objectResponse(object, key, wantHead);
    }
  }

  // 3) SPA fallback: extension-less, unmatched path -> the app shell index.html.
  //    Mirrors `_serve_product_site_file`'s SPA fallback (web_server.py:8752).
  //    Requests that look like a file (have an extension) get a hard 404 instead,
  //    so a missing asset is never masked by the HTML shell.
  if (!hasExtension(rel)) {
    key = prefix + "index.html";
    object = await getObject(env, key, request);
    if (object) {
      return objectResponse(object, key, wantHead);
    }
  }

  return notFound(slug, "file not found");
}

/**
 * Turn a request pathname into a safe relative key segment under the build root.
 * Returns "" for the site root, a slash-terminated string for directory requests,
 * or null if the path tries to escape (`..`, encoded traversal, absolute escape).
 */
function sanitizeRel(pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null; // malformed %-encoding
  }
  // Reject NUL and control chars.
  if (/[ -]/.test(decoded)) {
    return null;
  }
  const trailingSlash = decoded.endsWith("/") && decoded !== "/";
  // Strip the leading slash; normalise the rest by segments.
  const raw = decoded.replace(/^\/+/, "");
  const out = [];
  for (const seg of raw.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg === "..") return null; // never allow upward traversal
    if (seg.includes("\\")) return null; // no backslash smuggling
    out.push(seg);
  }
  let rel = out.join("/");
  if (rel !== "" && trailingSlash) rel += "/";
  return rel;
}

function hasExtension(rel) {
  const last = rel.split("/").pop() || "";
  const dot = last.lastIndexOf(".");
  return dot > 0; // leading-dot dotfiles don't count as an "extension" route
}

/**
 * Fetch an object from R2, honouring conditional request headers (ETag / range)
 * so the edge can answer 304 / 206 efficiently.
 */
function getObject(env, key, request) {
  const options = { onlyIf: request.headers };
  // Only ask R2 for a range when the client ACTUALLY sent a Range header. Passing
  // the headers unconditionally makes R2 report a full-size range for a plain GET,
  // which would make us answer a spurious `206 Partial Content` to a normal full
  // request (some clients/caches mishandle that). A real ranged request still works.
  if (request.headers.has("Range")) {
    options.range = request.headers;
  }
  return env.PRODUCT_SITES.get(key, options);
}

function extensionOf(key) {
  const last = key.split("/").pop() || "";
  const dot = last.lastIndexOf(".");
  return dot >= 0 ? last.slice(dot + 1).toLowerCase() : "";
}

function cacheControlFor(key) {
  // Hashed Vite assets live under `/assets/` and are content-addressed.
  if (key.includes("/assets/")) return IMMUTABLE_CACHE;
  const ext = extensionOf(key);
  if (ext === "html" || ext === "htm") return HTML_CACHE;
  return ASSET_CACHE;
}

function objectResponse(object, key, headOnly) {
  const headers = new Headers();
  // Let R2 write validators / content metadata, then layer our own.
  object.writeHttpMetadata(headers);
  if (object.httpEtag) headers.set("ETag", object.httpEtag);

  const ext = extensionOf(key);
  if (CONTENT_TYPES[ext]) {
    headers.set("Content-Type", CONTENT_TYPES[ext]);
  } else if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/octet-stream");
  }
  headers.set("Cache-Control", cacheControlFor(key));
  headers.set("X-Takyon-Edge", "r2-product-site");

  // R2 returns an R2ObjectBody (with .body) on a full/range GET, or a bare
  // R2Object (no body) when a conditional request matched -> 304.
  const hasBody = "body" in object && object.body !== undefined && object.body !== null;
  if (!hasBody) {
    // Conditional match: 304 Not Modified, no body.
    return new Response(null, { status: 304, headers });
  }

  const range = object.range;
  if (range && (range.offset !== undefined || range.length !== undefined)) {
    const size = object.size;
    const start = range.offset ?? 0;
    const length = range.length ?? size - start;
    const end = start + length - 1;
    // Cloudflare can attach `Range: bytes=0-` to an ordinary request; R2 then reports
    // a full-size range. Only answer 206 for a GENUINE partial range — a range that
    // actually covers the whole object falls through to a normal 200, so plain GETs
    // are never spuriously 206 (which some clients/caches mishandle).
    if (start > 0 || end < size - 1) {
      headers.set("Content-Range", `bytes ${start}-${end}/${size}`);
      headers.set("Accept-Ranges", "bytes");
      return new Response(headOnly ? null : object.body, { status: 206, headers });
    }
  }

  headers.set("Accept-Ranges", "bytes");
  return new Response(headOnly ? null : object.body, { status: 200, headers });
}

function notFound(slug, detail) {
  return new Response(
    JSON.stringify({ error: "product site file not found", business: slug, detail }),
    {
      status: 404,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
      },
    }
  );
}
