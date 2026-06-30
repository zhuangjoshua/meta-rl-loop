/**
 * check-internal-links.ts — find likely-broken internal links and orphan pages.
 *
 * Zero dependencies. Uses only Node built-ins. Read-only.
 *
 * Usage:
 *   node scripts/check-internal-links.ts [rootDir]
 *   npx tsx scripts/check-internal-links.ts .
 *
 * Builds a known-route set from file conventions (Next app/pages, Astro,
 * SvelteKit, Nuxt, Remix, static HTML), then scans all source/content files
 * for root-relative internal links ("/...") and reports:
 *   - brokenLinks:    "/..." links that resolve to no known route or public file
 *   - potentialOrphans: known static routes with zero inbound internal links
 *
 * Heuristic by design. Dynamic routes, rewrites/redirects, and links built from
 * variables in nav components can cause false positives — verify before acting.
 */
import { readdirSync, existsSync, readFileSync, statSync } from "node:fs";
import { join, extname, relative, basename, sep } from "node:path";

const root = process.argv[2] ? process.argv[2] : process.cwd();

const SCAN_EXTS = [
  ".html",
  ".htm",
  ".md",
  ".mdx",
  ".tsx",
  ".jsx",
  ".ts",
  ".js",
  ".astro",
  ".vue",
  ".svelte",
];
const ROUTE_EXTS = [".tsx", ".ts", ".jsx", ".js", ".mdx", ".md", ".astro", ".vue", ".html"];
const IGNORE_DIRS = new Set([
  "node_modules",
  ".git",
  ".next",
  ".nuxt",
  ".svelte-kit",
  ".astro",
  "dist",
  "build",
  "out",
  "coverage",
]);

function walk(dir: string, acc: string[] = []): string[] {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return acc;
  }
  for (const e of entries) {
    const full = join(dir, e.name);
    if (e.isDirectory()) {
      if (IGNORE_DIRS.has(e.name) || e.name.startsWith(".")) continue;
      walk(full, acc);
    } else if (e.isFile()) {
      acc.push(full);
    }
  }
  return acc;
}

function toPosix(p: string): string {
  return p.split(sep).join("/");
}

function normalizeRoute(parts: string[]): string {
  const cleaned = parts.filter(
    (p) =>
      p.length > 0 &&
      !(p.startsWith("(") && p.endsWith(")")) &&
      !p.startsWith("@") &&
      !p.startsWith("_"),
  );
  return "/" + cleaned.join("/").replace(/\/+/g, "/").replace(/^\/|\/$/g, "");
}

function firstExistingDir(...c: string[]): string | null {
  for (const d of c) if (existsSync(d)) return d;
  return null;
}

/** Compact route detector → set of normalized route strings (may include [param]/:param). */
function collectRoutes(files: string[]): Set<string> {
  const routes = new Set<string>();
  const add = (r: string) => routes.add(r);

  const appDir = firstExistingDir(join(root, "app"), join(root, "src/app"));
  const pagesDir = firstExistingDir(join(root, "pages"), join(root, "src/pages"));
  const svelteDir = firstExistingDir(join(root, "src/routes"));
  const remixDir = firstExistingDir(join(root, "app/routes"));
  const nuxtDir = firstExistingDir(join(root, "pages"), join(root, "src/pages"));

  for (const f of files) {
    const ext = extname(f);
    const name = basename(f).replace(ext, "");

    if (appDir && f.startsWith(appDir) && name === "page") {
      add(normalizeRoute(toPosix(relative(appDir, f)).split("/").slice(0, -1)));
      continue;
    }
    if (pagesDir && f.startsWith(pagesDir) && ROUTE_EXTS.includes(ext)) {
      const rel = toPosix(relative(pagesDir, f));
      if (!rel.startsWith("api/") && !["_app", "_document", "_error"].includes(name)) {
        const parts = rel.split("/");
        parts[parts.length - 1] = name === "index" ? "" : name;
        add(normalizeRoute(parts));
        continue;
      }
    }
    if (svelteDir && f.startsWith(svelteDir) && /^\+page\./.test(basename(f))) {
      add(normalizeRoute(toPosix(relative(svelteDir, f)).split("/").slice(0, -1)));
      continue;
    }
    if (remixDir && f.startsWith(remixDir) && ROUTE_EXTS.includes(ext)) {
      const rel = toPosix(relative(remixDir, f)).replace(ext, "");
      const parts = rel
        .split(".")
        .filter((s) => s !== "_index" && !s.startsWith("_"))
        .map((s) => (s.startsWith("$") ? `:${s.slice(1)}` : s));
      add(normalizeRoute(parts));
      continue;
    }
    if (ext === ".html") {
      const rel = toPosix(relative(root, f))
        .replace(/\.html$/, "")
        .replace(/^(public|static)\//, "");
      const parts = rel.split("/");
      if (parts[parts.length - 1] === "index") parts.pop();
      add(normalizeRoute(parts));
    }
  }
  // Always reachable.
  routes.add("/");
  return routes;
}

type SegmentKind = "literal" | "param" | "catchAll";
interface RouteMatcher {
  segments: { kind: SegmentKind; value: string }[];
}

/** Build segment-based matchers (no dynamic RegExp; avoids ReDoS by construction). */
function buildDynamicMatchers(routes: Set<string>): RouteMatcher[] {
  const matchers: RouteMatcher[] = [];
  for (const r of routes) {
    if (!/[\[:]/.test(r)) continue;
    const segments = r.split("/").map((seg) => {
      if (/^\[\.\.\..+\]$/.test(seg)) return { kind: "catchAll" as const, value: seg };
      if (/^\[.+\]$/.test(seg) || /^:.+$/.test(seg)) return { kind: "param" as const, value: seg };
      return { kind: "literal" as const, value: seg };
    });
    matchers.push({ segments });
  }
  return matchers;
}

/** Match a candidate path against a segment-based matcher without RegExp. */
function matchesRoute(candidate: string, matcher: RouteMatcher): boolean {
  const path = candidate.split("/");
  const segs = matcher.segments;
  let i = 0;
  for (let s = 0; s < segs.length; s++) {
    const seg = segs[s];
    if (seg.kind === "catchAll") {
      // Catch-all consumes the remainder; trailing segments after it (if any)
      // are not expressible in these route conventions, so accept the rest.
      return true;
    }
    if (i >= path.length) return false;
    if (seg.kind === "literal") {
      if (path[i] !== seg.value) return false;
    } else if (path[i].length === 0) {
      // param requires a non-empty segment ([^/]+).
      return false;
    }
    i++;
  }
  return i === path.length;
}

function extractLinks(content: string): string[] {
  const links: string[] = [];
  const md = /\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
  const attr = /(?:href|to|action)\s*=\s*["'`]([^"'`]+)["'`]/gi;
  let m: RegExpExecArray | null;
  while ((m = md.exec(content))) links.push(m[1]);
  while ((m = attr.exec(content))) links.push(m[1]);
  return links;
}

function normalizeTarget(t: string): string {
  let s = t.split("#")[0].split("?")[0].trim();
  if (s.length > 1) s = s.replace(/\/+$/, "");
  return s;
}

function isInternalRooted(t: string): boolean {
  if (!t.startsWith("/")) return false;
  if (t.startsWith("//")) return false; // protocol-relative external
  return true;
}

function publicHasFile(target: string): boolean {
  const candidates = [
    join(root, "public", target),
    join(root, "static", target),
    join(root, target),
  ];
  return candidates.some((p) => existsSync(p) && statSync(p).isFile());
}

function main() {
  if (!existsSync(root)) {
    console.error(`Root not found: ${root}`);
    process.exit(1);
  }
  const files = walk(root);
  const routes = collectRoutes(files);
  const staticRoutes = new Set([...routes].filter((r) => !/[\[:]/.test(r)));
  const dynamicMatchers = buildDynamicMatchers(routes);

  const inbound = new Map<string, number>();
  const broken: { link: string; from: string }[] = [];
  let relativeSkipped = 0;
  let externalSkipped = 0;

  for (const f of files) {
    if (!SCAN_EXTS.includes(extname(f))) continue;
    let content: string;
    try {
      content = readFileSync(f, "utf8");
    } catch {
      continue;
    }
    const from = toPosix(relative(root, f));
    for (const raw of extractLinks(content)) {
      const t = normalizeTarget(raw);
      if (!t || t.startsWith("#") || t.startsWith("mailto:") || t.startsWith("tel:") || t.startsWith("javascript:"))
        continue;
      if (/^https?:\/\//i.test(raw) || raw.startsWith("//")) {
        externalSkipped++;
        continue;
      }
      if (!isInternalRooted(t)) {
        relativeSkipped++;
        continue;
      }
      const fileExt = extname(t);
      if (fileExt && fileExt !== ".html") {
        // Asset-style link (/og.png, /sitemap.xml). Check it exists.
        if (!publicHasFile(t)) broken.push({ link: t, from });
        continue;
      }
      const candidate = t.replace(/\.html$/, "") || "/";
      inbound.set(candidate, (inbound.get(candidate) || 0) + 1);
      const matched =
        staticRoutes.has(candidate) ||
        dynamicMatchers.some((m) => matchesRoute(candidate, m)) ||
        publicHasFile(t);
      if (!matched) broken.push({ link: t, from });
    }
  }

  const potentialOrphans = [...staticRoutes].filter(
    (r) => r !== "/" && !(inbound.get(r) > 0),
  );

  const result = {
    knownRoutes: [...routes].sort(),
    routeCount: routes.size,
    brokenLinks: broken,
    brokenCount: broken.length,
    potentialOrphans,
    orphanCount: potentialOrphans.length,
    skipped: { relativeLinks: relativeSkipped, externalLinks: externalSkipped },
    note:
      "Heuristic. Dynamic routes, rewrites, and variable-built nav links can cause false positives. Relative links are not resolved.",
  };
  console.log(JSON.stringify(result, null, 2));
}

main();
