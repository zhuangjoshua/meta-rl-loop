import { readFile } from "node:fs/promises";
import path from "node:path";
import { ConfigurationError } from "../errors";

const SKILL_ROOT = path.join(process.cwd(), "skills", "takyon-company-factory");

export async function loadCompanyFactorySkill(skillFile: string) {
  const normalized = path.basename(skillFile);
  const fullPath = path.join(SKILL_ROOT, normalized);

  try {
    return await readFile(fullPath, "utf8");
  } catch (error) {
    throw new ConfigurationError(
      `Missing Takyon company skill file ${normalized}. Expected it at ${fullPath}.`
    );
  }
}
