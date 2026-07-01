/**
 * fs-scan.ts — shared filesystem traversal helpers for the SEO scripts.
 *
 * Zero dependencies. Uses only Node built-ins. Read-only.
 *
 * Extracts the directory-walk + ignore-set + target-collection logic that was
 * previously duplicated across check-metadata.ts, check-internal-links.ts, and
 * validate-jsonld.ts. Each caller supplies its own list of file extensions.
 */
import { readdirSync, existsSync, statSync } from "node:fs";
import { join, extname } from "node:path";

export const IGNORE_DIRS = new Set([
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

/**
 * Recursively walk `dir`, collecting file paths. When `exts` is provided, only
 * files whose extension is in the list are collected; otherwise all files are.
 */
export function walk(dir: string, exts?: string[], acc: string[] = []): string[] {
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
      walk(full, exts, acc);
    } else if (e.isFile()) {
      if (!exts || exts.includes(extname(e.name))) acc.push(full);
    }
  }
  return acc;
}

/**
 * Expand the given inputs (files or directories) into a deduplicated list of
 * file paths matching `exts`. Directories are walked recursively.
 */
export function collectTargets(args: string[], exts: string[]): string[] {
  const inputs = args.length ? args : [process.cwd()];
  const files = new Set<string>();
  for (const input of inputs) {
    if (!existsSync(input)) continue;
    if (statSync(input).isDirectory()) {
      for (const f of walk(input, exts)) files.add(f);
    } else if (exts.includes(extname(input))) {
      files.add(input);
    }
  }
  return [...files];
}
