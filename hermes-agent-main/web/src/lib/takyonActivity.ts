export interface TakyonDisplayRegistryEntry {
  display_name?: string;
}

export interface TakyonDisplayRegistry {
  tools?: Record<string, TakyonDisplayRegistryEntry>;
  skills?: Record<string, TakyonDisplayRegistryEntry>;
}

export function humanizeRegistryId(rawId: string): string {
  const trimmed = (rawId || "").trim();
  const name = trimmed
    .replace(/^takyon:/, "")
    .replace(/^business_/, "")
    .replace(/^tool:/, "")
    .split("/")
    .pop() || trimmed || "activity";
  return name
    .replace(/[._:-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function displayNameFromId(
  rawId?: string,
  registry?: TakyonDisplayRegistry,
  kind: "tools" | "skills" = "tools",
): { label: string; rawId: string; hasMetadata: boolean; meta?: TakyonDisplayRegistryEntry } {
  const id = (rawId || "").trim();
  const meta = id ? registry?.[kind]?.[id] : undefined;
  const displayName = (meta?.display_name || "").trim();
  return {
    label: displayName || humanizeRegistryId(id || "activity"),
    rawId: id,
    hasMetadata: Boolean(displayName),
    meta,
  };
}

export function metadataDebugDetail(rawId: string, hasMetadata: boolean, detail?: string): string {
  const id = rawId.trim();
  if (!id || hasMetadata) return detail || "";
  return [detail, `raw: ${id}`].filter(Boolean).join(" · ");
}

export function formatActivityLine(label: string, detail: string | undefined, status: string): string {
  return [label, detail, status].filter(Boolean).join(" · ");
}
