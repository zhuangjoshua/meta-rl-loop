import { surfaceContext } from "@takyon/surface-context.js";

export function businessDisplayName(): string {
  const raw = String(surfaceContext.business || "").trim().toLowerCase();
  const parts = raw.split(/[^a-z0-9]+/).filter(Boolean);
  if (!parts.length) return "Takyon app";
  return parts.map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}
