import { readFile } from "node:fs/promises";
import path from "node:path";
import { ConfigurationError } from "../errors";

const SKILL_PACKAGES = {
  "takyon-company-factory": path.join(process.cwd(), "skills", "takyon-company-factory"),
  "takyon-business-marketing": path.join(process.cwd(), "skills", "takyon-business-marketing")
} as const;

export type TakyonSkillPackage = keyof typeof SKILL_PACKAGES;

function safeSkillFile(skillFile: string) {
  const normalized = path.basename(skillFile);
  if (!normalized.endsWith(".md")) {
    throw new ConfigurationError(`Takyon skill file must be a Markdown file: ${skillFile}.`);
  }
  return normalized;
}

export async function loadTakyonSkill(skillPackage: TakyonSkillPackage, skillFile: string) {
  const normalized = safeSkillFile(skillFile);
  const root = SKILL_PACKAGES[skillPackage];
  const fullPath = path.join(root, normalized);

  try {
    return await readFile(fullPath, "utf8");
  } catch (error) {
    throw new ConfigurationError(
      `Missing Takyon skill file ${skillPackage}/${normalized}. Expected it at ${fullPath}.`
    );
  }
}

export async function loadCompanyFactorySkill(skillFile: string) {
  return loadTakyonSkill("takyon-company-factory", skillFile);
}

export async function loadBusinessMarketingSkill(skillFile: string) {
  return loadTakyonSkill("takyon-business-marketing", skillFile);
}
