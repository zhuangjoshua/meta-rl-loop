import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const root = process.cwd();
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
  "support-reply": "Draft a support reply grounded in provided context.",
  "business-marketing-context": "Create a Takyon-owned marketing context from explicit business evidence.",
  "business-search-visibility": "Create an SEO/GEO visibility scorecard and backlog without publishing changes.",
  "business-conversion-review": "Review conversion friction and propose bounded experiments.",
  "business-content-engine": "Create content pillars, page briefs, social angles, and draft copy.",
  "business-outreach-pipeline": "Create a no-sending outbound and sales pipeline plan.",
  "business-paid-media-review": "Review paid-media readiness and draft planning-only creative recommendations.",
  "business-measurement-plan": "Create an event taxonomy, attribution plan, and gated Pixel/CAPI audit plan."
};

const packages = [
  {
    namespace: "takyon-company-factory",
    sourceDir: path.join(root, "skills", "takyon-company-factory"),
    tags: "[takyon, company, operator, polsia, workflow]"
  },
  {
    namespace: "takyon-business-marketing",
    sourceDir: path.join(root, "skills", "takyon-business-marketing"),
    tags: "[takyon, business, marketing, sales, workflow]"
  }
];

function titleCase(value) {
  return value
    .split("-")
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

await mkdir(targetRoot, { recursive: true });

for (const skillPackage of packages) {
  const files = (await readdir(skillPackage.sourceDir))
    .filter((file) => file.endsWith(".md"))
    .sort();

  for (const file of files) {
    const base = file.replace(/\.md$/, "");
    const skillName = `${skillPackage.namespace}-${base}`;
    const targetDir = path.join(targetRoot, skillName);
    const body = await readFile(path.join(skillPackage.sourceDir, file), "utf8");
    const content = [
      "---",
      `name: ${skillName}`,
      `description: "${descriptions[base] || `Takyon ${titleCase(base)} workflow.`}"`,
      "version: 1.0.0",
      "metadata:",
      "  hermes:",
      `    tags: ${skillPackage.tags}`,
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
}
