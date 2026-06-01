/**
 * validate-jsonld.ts — extract and validate JSON-LD structured data.
 *
 * Zero dependencies. Uses only Node built-ins. Read-only.
 *
 * Usage:
 *   node scripts/validate-jsonld.ts <file-or-dir> [more ...]
 *   npx tsx scripts/validate-jsonld.ts public/ src/
 *   # with no args, scans the current directory
 *
 * For each <script type="application/ld+json"> block (and standalone
 * .json/.jsonld files) it reports: valid JSON?, has @context/@type?, the
 * declared @type(s), and a truthfulness flag for fields that imply claims
 * needing real backing (ratings, reviews, prices, offers). Schema built at
 * runtime (JSON.stringify / dangerouslySetInnerHTML) is reported as "runtime"
 * — validate it against rendered HTML instead.
 */
import { readdirSync, existsSync, readFileSync, statSync } from "node:fs";
import { join, extname, relative } from "node:path";

const SCAN_EXTS = [
  ".html",
  ".htm",
  ".tsx",
  ".jsx",
  ".ts",
  ".js",
  ".astro",
  ".vue",
  ".svelte",
  ".json",
  ".jsonld",
];
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

// Fields that assert facts which must be real, not fabricated.
const TRUTH_KEYS = [
  "aggregateRating",
  "ratingValue",
  "ratingCount",
  "reviewCount",
  "review",
  "offers",
  "price",
  "priceCurrency",
  "lowPrice",
  "highPrice",
];

type Block = {
  file: string;
  index: number;
  status: "valid" | "invalid" | "runtime";
  types: string[];
  errors: string[];
  warnings: string[];
  truthFlags: string[];
};

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
    } else if (e.isFile() && SCAN_EXTS.includes(extname(e.name))) {
      acc.push(full);
    }
  }
  return acc;
}

function collectTargets(args: string[]): string[] {
  const inputs = args.length ? args : [process.cwd()];
  const files = new Set<string>();
  for (const input of inputs) {
    if (!existsSync(input)) continue;
    if (statSync(input).isDirectory()) {
      for (const f of walk(input)) files.add(f);
    } else if (SCAN_EXTS.includes(extname(input))) {
      files.add(input);
    }
  }
  return [...files];
}

function findTruthKeys(node: unknown, found: Set<string>): void {
  if (Array.isArray(node)) {
    for (const item of node) findTruthKeys(item, found);
  } else if (node && typeof node === "object") {
    for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
      if (TRUTH_KEYS.includes(k)) found.add(k);
      findTruthKeys(v, found);
    }
  }
}

function nodesOf(parsed: unknown): Record<string, unknown>[] {
  const arr = Array.isArray(parsed) ? parsed : [parsed];
  const out: Record<string, unknown>[] = [];
  for (const n of arr) {
    if (n && typeof n === "object") {
      const obj = n as Record<string, unknown>;
      if (Array.isArray(obj["@graph"])) {
        for (const g of obj["@graph"] as unknown[])
          if (g && typeof g === "object") out.push(g as Record<string, unknown>);
      } else {
        out.push(obj);
      }
    }
  }
  return out;
}

function validateJson(file: string, index: number, text: string): Block {
  const errors: string[] = [];
  const warnings: string[] = [];
  const truth = new Set<string>();
  let types: string[] = [];

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    return {
      file,
      index,
      status: "invalid",
      types,
      errors: [`JSON parse error: ${(e as Error).message}`],
      warnings,
      truthFlags: [],
    };
  }

  const hasContext =
    (parsed && typeof parsed === "object" && "@context" in (parsed as object)) ||
    (Array.isArray(parsed) &&
      parsed.some((n) => n && typeof n === "object" && "@context" in n));
  if (!hasContext) warnings.push("missing @context");

  const nodes = nodesOf(parsed);
  if (nodes.length === 0) errors.push("no schema nodes found");
  for (const n of nodes) {
    const t = n["@type"];
    if (!t) warnings.push("a node is missing @type");
    else if (Array.isArray(t)) types.push(...(t as string[]).map(String));
    else types.push(String(t));
  }
  findTruthKeys(parsed, truth);

  return {
    file,
    index,
    status: errors.length ? "invalid" : "valid",
    types: [...new Set(types)],
    errors,
    warnings,
    truthFlags: [...truth],
  };
}

function extractBlocks(file: string, content: string): Block[] {
  const ext = extname(file);
  if (ext === ".json" || ext === ".jsonld") {
    return [validateJson(file, 0, content)];
  }

  const blocks: Block[] = [];
  const re =
    /<script\b([^>]*type=["']application\/ld\+json["'][^>]*)>([\s\S]*?)<\/script>/gi;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(content))) {
    const openTag = m[1] || "";
    const inner = (m[2] || "").trim();
    const runtime =
      inner === "" ||
      /\$\{|__html|JSON\.stringify|dangerouslySetInnerHTML/.test(inner) ||
      /__html|dangerouslySetInnerHTML/.test(openTag);
    if (runtime) {
      blocks.push({
        file,
        index: i,
        status: "runtime",
        types: [],
        errors: [],
        warnings: ["schema built at runtime — validate against rendered HTML"],
        truthFlags: [],
      });
    } else {
      blocks.push(validateJson(file, i, inner));
    }
    i++;
  }
  return blocks;
}

function main() {
  const targets = collectTargets(process.argv.slice(2));
  const blocks: Block[] = [];
  for (const file of targets) {
    let content: string;
    try {
      content = readFileSync(file, "utf8");
    } catch {
      continue;
    }
    const rel = relative(process.cwd(), file);
    blocks.push(...extractBlocks(rel, content));
  }

  const invalid = blocks.filter((b) => b.status === "invalid");
  const runtime = blocks.filter((b) => b.status === "runtime");
  const flagged = blocks.filter((b) => b.truthFlags.length > 0);

  const result = {
    blocksFound: blocks.length,
    valid: blocks.filter((b) => b.status === "valid").length,
    invalid: invalid.length,
    runtime: runtime.length,
    needsTruthCheck: flagged.length,
    note:
      "truthFlags mark fields (ratings/reviews/prices/offers) that must reflect REAL data — never fabricate them.",
    blocks,
  };
  console.log(JSON.stringify(result, null, 2));
  if (invalid.length > 0) process.exitCode = 1;
}

main();
