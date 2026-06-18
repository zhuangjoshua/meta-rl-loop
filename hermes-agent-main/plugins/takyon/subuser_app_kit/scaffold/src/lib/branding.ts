import { surfaceContext } from "@takyon/surface-context.js";

export function businessDisplayName(): string {
  const raw = String(surfaceContext.business || "").trim().toLowerCase();
  const parts = raw.split(/[^a-z0-9]+/).filter(Boolean);
  if (!parts.length) return "Takyon app";
  return parts.map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

// Free brand mark injected by the surface-context payload (no paid logo call at bootstrap).
// A later credit-gated business_generate_logo run replaces the seeded favicon, but these
// remain the deterministic fallback for the product UI.
export function brandAccent(): string {
  return String((surfaceContext as Record<string, unknown>).brandAccent || "#2563eb");
}

export function brandMarkSvg(): string {
  return String((surfaceContext as Record<string, unknown>).brandMarkSvg || "");
}

export function brandMarkDataUri(): string {
  const svg = brandMarkSvg();
  if (!svg) return "/favicon.svg";
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}
