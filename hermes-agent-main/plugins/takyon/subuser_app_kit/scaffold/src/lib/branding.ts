import { surfaceContext } from "@takyon/surface-context.js";

export function businessDisplayName(): string {
  const raw = String(
    (surfaceContext as Record<string, unknown>).businessName || "",
  ).trim();
  const parts = raw.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  if (!parts.length) return "Product";
  if (raw === raw.toLowerCase()) {
    return parts.map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
  }
  return raw;
}

// Free brand mark injected by the surface-context payload (no paid logo call at bootstrap).
// A later credit-gated business_generate_logo run publishes a real logo PNG into public/ and sets
// brandLogoUrl; brandMarkDataUri() then prefers it. The monogram remains the deterministic fallback.
export function brandAccent(): string {
  return String((surfaceContext as Record<string, unknown>).brandAccent || "#2563eb");
}

export function brandMarkSvg(): string {
  return String((surfaceContext as Record<string, unknown>).brandMarkSvg || "");
}

// "/brand-logo.png" once a paid logo has been published into the site's public/ dir, else "".
export function brandLogoUrl(): string {
  return String((surfaceContext as Record<string, unknown>).brandLogoUrl || "");
}

export function brandMarkDataUri(): string {
  // Prefer the published (paid) logo so the generated brand mark replaces the bootstrap monogram
  // in the header and landing once business_generate_logo has run.
  const logo = brandLogoUrl();
  if (logo) return logo;
  const svg = brandMarkSvg();
  if (!svg) return "/favicon.svg";
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

// Idea-derived hero copy, injected by the surface-context payload from the bootstrap brief so the
// FIRST published landing (before the slower full design pass) is already branded to the business,
// not a generic "Welcome to X". Each returns "" when unset, so the landing falls back cleanly.
export function brandHeroEyebrow(): string {
  return String((surfaceContext as Record<string, unknown>).heroEyebrow || "").trim();
}

export function brandHeroHeadline(): string {
  return String((surfaceContext as Record<string, unknown>).heroHeadline || "").trim();
}

export function brandHeroSubhead(): string {
  return String((surfaceContext as Record<string, unknown>).heroSubhead || "").trim();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function dollars(value: unknown): string | null {
  return typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(2)}` : null;
}

/** "$X of $Y used this week" line from an account's weekly usage allowance. Returns null when no
 *  allowance is present so callers can hide the line instead of faking a quota. Single source of
 *  truth for account usage-allowance formatting across screens. */
export function formatUsageAllowance(account: unknown): string | null {
  const acct = isRecord(account) ? account : null;
  const allocation = acct && isRecord(acct.usage_allocation)
    ? (acct.usage_allocation as Record<string, unknown>)
    : null;
  if (!allocation) return null;
  const used = dollars(allocation.committed_usd);
  if (used === null) return null;
  const limit = dollars(allocation.hard_limit_usd);
  return limit ? `${used} of ${limit} used this week` : `${used} used this week`;
}
