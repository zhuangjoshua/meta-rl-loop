import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const root = process.cwd();
const sourceDir = path.join(root, "skills", "takyon-company-factory");
const hermesHome = process.env.HERMES_HOME || path.join(root, ".argon-hermes-home");
const targetRoot = path.join(hermesHome, "skills");

const descriptions = {
  "activity-review": "Review company activity, revenue, logs, risks, and recommend the next workflow.",
  "business-plan": "Refine a raw company idea into buyer, pain, offer, first workflow, and build plan.",
  "ceo-awake": "Run the stateless CEO awake loop over explicit company state and task queue.",
  "content-generation": "Generate grounded SEO, docs, support, or landing content for a generated company.",
  "find-leads": "Find lead-source hypotheses, qualification rules, and search queries without fabricating contacts.",
  "market-research": "Research customer, competitor, pricing, and pain evidence for a generated company.",
  "meta-creative": "Generate Meta creative direction and copy without fake claims or fake media.",
  "outreach-copy": "Generate outbound copy variants that can be approved before vendor sending.",
  "site-build": "Build the first customer-facing generated product/site surface.",
  "site-improve": "Improve an existing generated product/site surface with validation.",
  "social-posting": "Generate a channel-safe X or Meta post draft without publishing.",
  "support-reply": "Draft a support reply grounded in provided context."
};

function titleCase(value) {
  return value
    .split("-")
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

const files = (await readdir(sourceDir))
  .filter((file) => file.endsWith(".md"))
  .sort();

await mkdir(targetRoot, { recursive: true });

for (const file of files) {
  const base = file.replace(/\.md$/, "");
  const skillName = `takyon-company-factory-${base}`;
  const targetDir = path.join(targetRoot, skillName);
  const body = await readFile(path.join(sourceDir, file), "utf8");
  const content = [
    "---",
    `name: ${skillName}`,
    `description: "${descriptions[base] || `Takyon company factory ${titleCase(base)} workflow.`}"`,
    "version: 1.0.0",
    "metadata:",
    "  hermes:",
    "    tags: [takyon, company, operator, polsia, workflow]",
    "    related_skills: []",
    "---",
    "",
    body.trim(),
    ""
  ].join("\n");

  await mkdir(targetDir, { recursive: true });
  await writeFile(path.join(targetDir, "SKILL.md"), content, "utf8");
  console.log(`synced ${skillName}`);
}
