import fs from "node:fs";
import path from "node:path";

let loaded = false;

function unquote(raw: string) {
  const value = raw.trim();
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    return value.slice(1, -1).replace(/\\n/g, "\n");
  }
  return value;
}

export function loadLocalSecrets() {
  if (loaded || process.env.VERCEL === "1") return;
  loaded = true;

  const envPath = path.join(process.cwd(), ".env.local");
  if (!fs.existsSync(envPath)) return;

  const text = fs.readFileSync(envPath, "utf8");
  for (const line of text.split(/\r?\n/)) {
    if (!line || line.trimStart().startsWith("#")) continue;
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) continue;
    const [, key, raw] = match;
    if (process.env[key] === undefined) {
      process.env[key] = unquote(raw);
    }
  }
}

function quoteEnv(value: string) {
  return JSON.stringify(value);
}

export function upsertLocalSecret(key: string, value: string) {
  const normalized = key.trim();
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(normalized)) {
    throw new Error("Secret key must be a valid environment variable name.");
  }
  const envPath = path.join(process.cwd(), ".env.local");
  const existing = fs.existsSync(envPath) ? fs.readFileSync(envPath, "utf8") : "";
  const lines = existing ? existing.split(/\r?\n/) : [];
  const nextLine = `${normalized}=${quoteEnv(value)}`;
  let replaced = false;
  const updated = lines.map((line) => {
    if (line.match(new RegExp(`^${normalized}=`))) {
      replaced = true;
      return nextLine;
    }
    return line;
  });
  if (!replaced) {
    if (updated.length && updated[updated.length - 1] !== "") updated.push("");
    updated.push(nextLine);
  }
  fs.writeFileSync(envPath, updated.join("\n").replace(/\n*$/, "\n"), "utf8");
  process.env[normalized] = value;
  return envPath;
}
