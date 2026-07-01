/**
 * check-metadata.ts — extract and lint on-page SEO metadata for a set of pages.
 *
 * Zero dependencies. Uses only Node built-ins. Read-only.
 *
 * Usage:
 *   node scripts/check-metadata.ts <file-or-dir> [more ...]
 *   npx tsx scripts/check-metadata.ts public/index.html src/pages
 *   # with no args, scans the current directory for page-like files
 *
 * Reports, per page: title, meta description, H1 count, canonical, OG, Twitter,
 * JSON-LD presence, rough word count, and a list of issues.
 *
 * HTML files are parsed accurately via regex. Framework source files
 * (.tsx/.jsx/.astro/.vue/.svelte/.md/.mdx) are scanned heuristically — confirm
 * findings by reading the file or rendering the page before acting on them.
 */
import { readFileSync } from "node:fs";
import { extname, relative } from "node:path";
import { collectTargets } from "./lib/fs-scan.ts";

const TITLE_MAX = 60;
const TITLE_MIN = 15;
const DESC_MAX = 160; // target ~155
const DESC_MIN = 50;
const THIN_WORDS = 250;

const PAGE_EXTS = [
  ".html",
  ".htm",
  ".tsx",
  ".jsx",
  ".ts",
  ".js",
  ".astro",
  ".vue",
  ".svelte",
  ".md",
  ".mdx",
];

type Finding = {
  file: string;
  kind: "html" | "source";
  title: string | null;
  titleLen: number;
  description: string | null;
  descLen: number;
  h1Count: number;
  canonical: boolean;
  openGraph: boolean;
  twitter: boolean;
  jsonLd: boolean;
  words: number | null;
  issues: string[];
};

function parseAttrs(tag: string): Record<string, string> {
  const attrs: Record<string, string> = {};
  const re = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(tag))) {
    attrs[m[1].toLowerCase()] = m[3] ?? m[4] ?? m[5] ?? "";
  }
  return attrs;
}

function decode(s: string): string {
  return s
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function countWords(text: string): number {
  const cleaned = text
    .replace(/```[\s\S]*?```/g, " ") // code fences
    .replace(/<[^>]+>/g, " ") // tags
    .replace(/[#*_>`|-]/g, " ");
  const tokens = cleaned.split(/\s+/).filter((t) => /[a-zA-Z0-9]/.test(t));
  return tokens.length;
}

function analyzeHtml(file: string, html: string): Finding {
  const titleMatch = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  const title = titleMatch ? decode(titleMatch[1]) : null;

  const metaTags = html.match(/<meta\b[^>]*>/gi) || [];
  let description: string | null = null;
  let openGraph = false;
  let twitter = false;
  for (const tag of metaTags) {
    const a = parseAttrs(tag);
    const name = a["name"] || "";
    const prop = a["property"] || "";
    if (name.toLowerCase() === "description") description = decode(a["content"] || "");
    if (prop.toLowerCase().startsWith("og:")) openGraph = true;
    if (name.toLowerCase().startsWith("twitter:")) twitter = true;
  }

  const linkTags = html.match(/<link\b[^>]*>/gi) || [];
  const canonical = linkTags.some(
    (t) => (parseAttrs(t)["rel"] || "").toLowerCase() === "canonical",
  );

  const h1Count = (html.match(/<h1[\s>]/gi) || []).length;
  const jsonLd = /<script[^>]+type=["']application\/ld\+json["']/i.test(html);
  const words = countWords(html.replace(/<head[\s\S]*?<\/head>/i, " "));

  return finalize({
    file,
    kind: "html",
    title,
    titleLen: title ? title.length : 0,
    description,
    descLen: description ? description.length : 0,
    h1Count,
    canonical,
    openGraph,
    twitter,
    jsonLd,
    words,
    issues: [],
  });
}

function firstGroup(src: string, patterns: RegExp[]): string | null {
  for (const re of patterns) {
    const m = src.match(re);
    if (m && m[1]) return decode(m[1]);
  }
  return null;
}

function analyzeSource(file: string, src: string): Finding {
  const ext = extname(file);
  const isMd = ext === ".md" || ext === ".mdx";

  // Frontmatter (Astro/markdown) and JS object metadata are common carriers.
  const title = firstGroup(src, [
    /<title[^>]*>([\s\S]*?)<\/title>/i,
    /^\s*title\s*[:=]\s*["'`]([^"'`]+)["'`]/im,
    /\btitle\s*:\s*["'`]([^"'`]+)["'`]/i,
  ]);
  const description = firstGroup(src, [
    /^\s*description\s*[:=]\s*["'`]([^"'`]+)["'`]/im,
    /\bdescription\s*:\s*["'`]([^"'`]+)["'`]/i,
    /name=["']description["'][^>]*content=["']([^"']+)["']/i,
  ]);

  const canonical = /canonical/i.test(src);
  const openGraph = /og:|openGraph|opengraph/i.test(src);
  const twitter = /twitter:|twitter\s*[:=]/i.test(src);
  const jsonLd = /application\/ld\+json|@context|jsonLd|json-ld/i.test(src);

  let h1Count = (src.match(/<h1[\s>]/gi) || []).length;
  if (isMd) {
    const body = src.replace(/^---[\s\S]*?---/, "");
    h1Count += (body.match(/^#\s+\S/gm) || []).length;
  }

  const words = isMd ? countWords(src.replace(/^---[\s\S]*?---/, "")) : null;

  return finalize({
    file,
    kind: "source",
    title,
    titleLen: title ? title.length : 0,
    description,
    descLen: description ? description.length : 0,
    h1Count,
    canonical,
    openGraph,
    twitter,
    jsonLd,
    words,
    issues: [],
  });
}

function finalize(f: Finding): Finding {
  const issues: string[] = [];
  if (!f.title) issues.push("missing title");
  else if (f.titleLen > TITLE_MAX)
    issues.push(`title too long (${f.titleLen} > ${TITLE_MAX})`);
  else if (f.titleLen < TITLE_MIN)
    issues.push(`title may be too short/thin (${f.titleLen} chars)`);

  if (!f.description) issues.push("missing meta description");
  else if (f.descLen > DESC_MAX)
    issues.push(`meta description too long (${f.descLen} > ${DESC_MAX})`);
  else if (f.descLen < DESC_MIN)
    issues.push(`meta description may be too short (${f.descLen} chars)`);

  if (f.h1Count === 0) issues.push("no H1 found");
  else if (f.h1Count > 1) issues.push(`multiple H1s (${f.h1Count})`);

  if (!f.canonical) issues.push("no canonical detected");
  if (!f.openGraph) issues.push("no OpenGraph metadata detected");
  if (!f.twitter) issues.push("no Twitter card metadata detected");
  if (!f.jsonLd) issues.push("no JSON-LD/schema detected");
  if (f.words !== null && f.words < THIN_WORDS)
    issues.push(`thin content (~${f.words} words < ${THIN_WORDS})`);

  f.issues = issues;
  return f;
}

function main() {
  const targets = collectTargets(process.argv.slice(2), PAGE_EXTS);
  const findings: Finding[] = [];
  for (const file of targets) {
    let content: string;
    try {
      content = readFileSync(file, "utf8");
    } catch {
      continue;
    }
    const ext = extname(file);
    const rel = relative(process.cwd(), file);
    findings.push(
      ext === ".html" || ext === ".htm"
        ? analyzeHtml(rel, content)
        : analyzeSource(rel, content),
    );
  }

  const withIssues = findings.filter((f) => f.issues.length > 0).length;
  const result = {
    scanned: findings.length,
    pagesWithIssues: withIssues,
    note: "Source-file results are heuristic; confirm before editing.",
    findings: findings.sort((a, b) => b.issues.length - a.issues.length),
  };
  console.log(JSON.stringify(result, null, 2));
}

main();
