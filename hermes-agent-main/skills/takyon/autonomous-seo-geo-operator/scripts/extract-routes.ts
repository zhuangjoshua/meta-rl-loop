/**
 * extract-routes.ts — detect the web framework and enumerate page routes from a repo.
 *
 * Zero dependencies. Uses only Node built-ins. Read-only.
 *
 * Usage:
 *   node scripts/extract-routes.ts [rootDir]          # Node >= 22.6 strips types
 *   npx tsx scripts/extract-routes.ts [rootDir]       # fallback runner
 *
 * Output: JSON { framework, root, count, routes: [{ route, file }] } to stdout.
 *
 * This is a heuristic mapper. It covers the common conventions of Next.js
 * (app + pages router), Astro, SvelteKit, Nuxt, Remix/React-Router, Gatsby,
 * Vite, and plain static HTML. Treat the output as a starting map, not gospel.
 */
import { readdirSync, existsSync, readFileSync } from "node:fs";
import { join, relative, extname, basename, sep } from "node:path";

type Framework =
  | "next"
  | "astro"
  | "sveltekit"
  | "nuxt"
  | "remix"
  | "gatsby"
  | "vite"
  | "static"
  | "unknown";

type Route = { route: string; file: string };

const IGNORE_DIRS = new Set([
  "node_modules",
  ".git",
  ".next",
  ".nuxt",
  ".svelte-kit",
  ".astro",
  ".vercel",
  ".netlify",
  ".cache",
  ".turbo",
  "dist",
  "build",
  "out",
  "coverage",
  "tmp",
  "vendor",
]);

const root = process.argv[2] ? process.argv[2] : process.cwd();

function toPosix(p: string): string {
  return p.split(sep).join("/");
}

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

function readPkgDeps(): Record<string, string> {
  const pkgPath = join(root, "package.json");
  if (!existsSync(pkgPath)) return {};
  try {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf8"));
    return { ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}) };
  } catch {
    return {};
  }
}

function hasConfig(prefix: string): boolean {
  return ["js", "mjs", "cjs", "ts", "mts"].some((ext) =>
    existsSync(join(root, `${prefix}.${ext}`)),
  );
}

function detectFramework(deps: Record<string, string>): Framework {
  const has = (n: string) => Object.prototype.hasOwnProperty.call(deps, n);
  if (has("next") || hasConfig("next.config")) return "next";
  if (
    has("@remix-run/react") ||
    has("@remix-run/node") ||
    has("@react-router/dev")
  )
    return "remix";
  if (has("astro") || hasConfig("astro.config")) return "astro";
  if (has("@sveltejs/kit") || hasConfig("svelte.config")) return "sveltekit";
  if (has("nuxt") || has("nuxt3") || hasConfig("nuxt.config")) return "nuxt";
  if (has("gatsby")) return "gatsby";
  if (has("vite") || hasConfig("vite.config")) return "vite";
  return "static";
}

function cleanSegments(parts: string[]): string[] {
  // Drop route groups "(group)", private "_dir", and parallel/intercept slots.
  return parts.filter(
    (p) =>
      p.length > 0 &&
      !(p.startsWith("(") && p.endsWith(")")) &&
      !p.startsWith("@") &&
      !p.startsWith("_"),
  );
}

function normalizeRoute(segments: string[]): string {
  const joined = segments.join("/");
  return "/" + joined.replace(/\/+/g, "/").replace(/^\/|\/$/g, "");
}

function firstExistingDir(...candidates: string[]): string | null {
  for (const c of candidates) if (existsSync(c)) return c;
  return null;
}

function extractNext(files: string[]): Route[] {
  const routes: Route[] = [];
  // App Router: any "page.{ext}" or "route.{ext}" under app/ or src/app/
  const appDir = firstExistingDir(join(root, "app"), join(root, "src/app"));
  if (appDir) {
    for (const f of files) {
      if (!f.startsWith(appDir)) continue;
      const name = basename(f).replace(extname(f), "");
      if (name !== "page") continue;
      const rel = toPosix(relative(appDir, f));
      const parts = cleanSegments(rel.split("/").slice(0, -1));
      routes.push({ route: normalizeRoute(parts), file: toPosix(relative(root, f)) });
    }
  }
  // Pages Router: files under pages/ or src/pages/ (excluding api/ and specials)
  const pagesDir = firstExistingDir(join(root, "pages"), join(root, "src/pages"));
  if (pagesDir) {
    for (const f of files) {
      if (!f.startsWith(pagesDir)) continue;
      const ext = extname(f);
      if (![".tsx", ".ts", ".jsx", ".js", ".mdx", ".md"].includes(ext)) continue;
      const rel = toPosix(relative(pagesDir, f));
      if (rel.startsWith("api/")) continue;
      const name = basename(f).replace(ext, "");
      if (["_app", "_document", "_error", "404", "500"].includes(name)) continue;
      let parts = rel.split("/");
      parts[parts.length - 1] = name === "index" ? "" : name;
      routes.push({
        route: normalizeRoute(cleanSegments(parts)),
        file: toPosix(relative(root, f)),
      });
    }
  }
  return routes;
}

function extractByPagesDir(
  files: string[],
  baseDir: string | null,
  exts: string[],
): Route[] {
  if (!baseDir) return [];
  const routes: Route[] = [];
  for (const f of files) {
    if (!f.startsWith(baseDir)) continue;
    const ext = extname(f);
    if (!exts.includes(ext)) continue;
    const rel = toPosix(relative(baseDir, f));
    const name = basename(f).replace(ext, "");
    let parts = rel.split("/");
    parts[parts.length - 1] = name === "index" ? "" : name;
    routes.push({
      route: normalizeRoute(cleanSegments(parts)),
      file: toPosix(relative(root, f)),
    });
  }
  return routes;
}

function extractSvelteKit(files: string[]): Route[] {
  const baseDir = firstExistingDir(join(root, "src/routes"));
  if (!baseDir) return [];
  const routes: Route[] = [];
  for (const f of files) {
    if (!f.startsWith(baseDir)) continue;
    const name = basename(f);
    if (!/^\+page\.(svelte|md|svx)$/.test(name)) continue;
    const rel = toPosix(relative(baseDir, f));
    const parts = cleanSegments(rel.split("/").slice(0, -1));
    routes.push({ route: normalizeRoute(parts), file: toPosix(relative(root, f)) });
  }
  return routes;
}

function extractRemix(files: string[]): Route[] {
  const baseDir = firstExistingDir(join(root, "app/routes"));
  if (!baseDir) return [];
  const routes: Route[] = [];
  for (const f of files) {
    if (!f.startsWith(baseDir)) continue;
    const ext = extname(f);
    if (![".tsx", ".ts", ".jsx", ".js", ".mdx", ".md"].includes(ext)) continue;
    const rel = toPosix(relative(baseDir, f)).replace(ext, "");
    // Flat routes (v2): "_index" -> "/", dots are separators, "$param" -> ":param".
    const flat = rel
      .replace(/\/route$/, "")
      .split(".")
      .filter((s) => s !== "_index" && !s.startsWith("_"))
      .map((s) => (s.startsWith("$") ? `:${s.slice(1)}` : s));
    routes.push({
      route: normalizeRoute(flat),
      file: toPosix(relative(root, f)),
    });
  }
  return routes;
}

function extractStatic(files: string[]): Route[] {
  const routes: Route[] = [];
  for (const f of files) {
    if (extname(f) !== ".html") continue;
    // public/ and static/ are conventional web roots, not URL segments.
    const rel = toPosix(relative(root, f))
      .replace(/\.html$/, "")
      .replace(/^(public|static)\//, "");
    const parts = rel.split("/");
    if (parts[parts.length - 1] === "index") parts.pop();
    routes.push({ route: normalizeRoute(parts), file: toPosix(relative(root, f)) });
  }
  return routes;
}

function dedupe(routes: Route[]): Route[] {
  const seen = new Set<string>();
  const out: Route[] = [];
  for (const r of routes.sort((a, b) => a.route.localeCompare(b.route))) {
    const key = r.route + "::" + r.file;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(r);
  }
  return out;
}

function main() {
  if (!existsSync(root)) {
    console.error(`Root not found: ${root}`);
    process.exit(1);
  }
  const deps = readPkgDeps();
  const framework = detectFramework(deps);
  const files = walk(root);

  let routes: Route[] = [];
  switch (framework) {
    case "next":
      routes = extractNext(files);
      break;
    case "astro":
      routes = extractByPagesDir(
        files,
        firstExistingDir(join(root, "src/pages")),
        [".astro", ".md", ".mdx", ".html"],
      );
      break;
    case "nuxt":
      routes = extractByPagesDir(
        files,
        firstExistingDir(join(root, "pages"), join(root, "src/pages")),
        [".vue"],
      );
      break;
    case "sveltekit":
      routes = extractSvelteKit(files);
      break;
    case "remix":
      routes = extractRemix(files);
      break;
    case "gatsby":
      routes = extractByPagesDir(
        files,
        firstExistingDir(join(root, "src/pages")),
        [".tsx", ".ts", ".jsx", ".js", ".mdx", ".md"],
      );
      break;
    default:
      routes = extractStatic(files);
  }

  // Always include any top-level static HTML as a fallback signal.
  if (framework !== "static") {
    const staticRoutes = extractStatic(files.filter((f) => f.includes(`${sep}public${sep}`)));
    routes = routes.concat(staticRoutes);
  }

  const result = {
    framework,
    root: toPosix(root),
    count: dedupe(routes).length,
    routes: dedupe(routes),
  };
  console.log(JSON.stringify(result, null, 2));
}

main();
