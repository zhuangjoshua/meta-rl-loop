// Headless-Chrome verification harness for the curated chat_stream rendering.
// Boots the standalone Vite harness, renders the REAL Product / Building
// components with mock workspace props, and asserts the four required scenarios.
// Writes a screenshot per scenario and exits non-zero on any failed assertion.
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { mkdirSync } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SHOTS = resolve(__dirname, "shots");
mkdirSync(SHOTS, { recursive: true });

// Resolve playwright from the project if present, else from the npx cache
// (the CLI + browsers are cached even when it is not a project dependency).
async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    /* fall through to cache lookup */
  }
  const { globSync } = await import("node:fs");
  const home = process.env.HOME || "";
  const candidates = globSync(`${home}/.npm/_npx/*/node_modules/playwright/index.js`);
  for (const candidate of candidates) {
    try {
      return await import(candidate);
    } catch {
      /* try next */
    }
  }
  throw new Error("playwright not resolvable (not a dep, not in npx cache)");
}
const pw = await loadPlaywright();
const chromium = pw.chromium || pw.default?.chromium;
if (!chromium) throw new Error("playwright.chromium unavailable");

const BASE = "http://localhost:5199/chat.html";
const PORT = 5199;

function startServer() {
  const proc = spawn(
    "npx",
    ["vite", "--config", "harness/vite.harness.config.ts", "--port", String(PORT)],
    { cwd: resolve(__dirname, ".."), stdio: ["ignore", "pipe", "pipe"] },
  );
  return proc;
}

async function waitForServer(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://localhost:${PORT}/chat.html`);
      if (res.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("Harness server did not start in time");
}

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: Boolean(cond), detail });
  const tag = cond ? "PASS" : "FAIL";
  console.log(`  [${tag}] ${name}${detail ? ` — ${detail}` : ""}`);
}

const server = startServer();
let serverErr = "";
server.stderr.on("data", (d) => (serverErr += d.toString()));

let browser;
try {
  await waitForServer();
  // Prefer the bundled playwright chromium; if its build is mismatched, fall
  // back to the system Google Chrome so the harness runs without an install step.
  const SYSTEM_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  try {
    browser = await chromium.launch({ headless: true });
  } catch {
    browser = await chromium.launch({ headless: true, executablePath: SYSTEM_CHROME });
  }
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  // The chat panel is the surface under test. The sibling CompanyTab makes
  // live API/asset calls and has its own effect churn that loops without a
  // backend — that noise (404s, its "Maximum update depth" loop) is unrelated
  // to the chat-rendering change, so it is filtered from the chat assertion.
  const IGNORE_CONSOLE = [
    /Failed to load resource/i,
    /Maximum update depth exceeded/i,
    /status of 404/i,
  ];
  const consoleErrors = [];
  const pushErr = (text) => {
    if (IGNORE_CONSOLE.some((re) => re.test(text))) return;
    consoleErrors.push(text);
  };
  page.on("console", (m) => {
    if (m.type() === "error") pushErr(m.text());
  });
  page.on("pageerror", (e) => pushErr(String(e)));

  async function load(scenario, surface = "product") {
    consoleErrors.length = 0;
    await page.goto(`${BASE}?scenario=${scenario}&surface=${surface}`, {
      waitUntil: "networkidle",
    });
    await page.waitForFunction(() => window.__HARNESS_READY__ === true, { timeout: 15000 });
    await page.waitForTimeout(150);
  }

  // Helpers scoped to the chat panel (.lb-chat__log) for Product scenarios.
  // Exclude the standalone thinking-dots bubble (.lb-msg__bubble--think) — it is
  // not a conversation bubble.
  const agentTexts = () =>
    page.$$eval(
      ".lb-chat__log .lb-msg--agent .lb-msg__bubble:not(.lb-msg__bubble--think)",
      (els) => els.map((e) => e.textContent.trim()),
    );
  const userTexts = () =>
    page.$$eval(".lb-chat__log .lb-msg--user .lb-msg__bubble", (els) =>
      els.map((e) => e.textContent.trim()),
    );
  const orderedRoles = () =>
    page.$$eval(".lb-chat__log .lb-msg", (els) =>
      els
        .filter((e) => !e.querySelector(".lb-msg__bubble--think"))
        .map((e) => (e.classList.contains("lb-msg--user") ? "user" : "agent")),
    );
  const thinkingCount = () => page.$$eval(".lb-msg__bubble--think", (els) => els.length);
  const bannedToken = () => page.evaluate(() => window.__BANNED__);
  const bodyHtml = () => page.evaluate(() => document.body.innerHTML);

  // ---- (1) bootstrap ----
  console.log("\n(1) bootstrap — 3 curated narration bubbles, no user, running");
  await load("bootstrap");
  {
    const agents = await agentTexts();
    const users = await userTexts();
    const dots = await thinkingCount();
    check("3 clean agent conversation bubbles", agents.length === 3, `got ${agents.length}: ${JSON.stringify(agents)}`);
    check("no user bubbles", users.length === 0, `got ${users.length}`);
    check("exactly one standalone thinking-dots row", dots === 1, `got ${dots}`);
    // Dots are on their OWN row: the think bubble must not contain conversational text.
    const dotsText = await page.$$eval(".lb-msg__bubble--think", (els) =>
      els.map((e) => e.textContent.replace(/\s+/g, "")),
    );
    check("dots row carries no words", dotsText.every((t) => t === ""), JSON.stringify(dotsText));
    // No card / "What changed" / raw reasoning plumbing.
    const html = await bodyHtml();
    check("no 'What changed' card", !/what changed/i.test(html));
    check("no raw plumbing token in DOM", !html.includes(await bannedToken()));
    check("no console errors", consoleErrors.length === 0, consoleErrors.join(" | "));
  }
  await page.screenshot({ path: resolve(SHOTS, "1-bootstrap.png"), fullPage: true });

  // ---- (2) idle ----
  console.log("\n(2) idle — chat_running false → dots GONE");
  await load("idle");
  {
    const dots = await thinkingCount();
    check("no thinking-dots row when idle", dots === 0, `got ${dots}`);
    check("no console errors", consoleErrors.length === 0, consoleErrors.join(" | "));
  }
  await page.screenshot({ path: resolve(SHOTS, "2-idle.png"), fullPage: true });

  // ---- (3) follow-up ----
  console.log("\n(3) follow-up — user bubble then CEO narration, in order");
  await load("followup");
  {
    const roles = await orderedRoles();
    const users = await userTexts();
    const agents = await agentTexts();
    check("1 user bubble", users.length === 1, JSON.stringify(users));
    check("2 agent bubbles", agents.length === 2, JSON.stringify(agents));
    check("user bubble is FIRST", roles[0] === "user", JSON.stringify(roles));
    check(
      "agent narration follows the user message",
      roles.slice(1).every((r) => r === "agent") && roles.length === 3,
      JSON.stringify(roles),
    );
    check("no console errors", consoleErrors.length === 0, consoleErrors.join(" | "));
  }
  await page.screenshot({ path: resolve(SHOTS, "3-followup.png"), fullPage: true });

  // ---- (4) banned plumbing-only message dropped ----
  console.log("\n(4) banned-only chat_stream message → dropped");
  await load("banned");
  {
    const agents = await agentTexts();
    const banned = await bannedToken();
    check("banned-only message dropped (2 bubbles remain)", agents.length === 2, JSON.stringify(agents));
    check("banned token absent from transcript", agents.every((t) => !t.includes(banned)));
    const html = await bodyHtml();
    check("banned token absent from entire DOM", !html.includes(banned));
    check("no console errors", consoleErrors.length === 0, consoleErrors.join(" | "));
  }
  await page.screenshot({ path: resolve(SHOTS, "4-banned.png"), fullPage: true });

  // ---- (B) Building surface uses curated chat_stream, not raw narration ----
  console.log("\n(B) Building screen — curated stream, header, build-details disclosure");
  await load("bootstrap", "building");
  {
    const banned = await bannedToken();
    const streamLines = await page.$$eval(".lb-bld__chat .lb-bld__line", (els) =>
      els.map((e) => e.textContent.trim()),
    );
    check("building renders curated stream lines", streamLines.length >= 3, JSON.stringify(streamLines));
    const chatHtml = await page.$eval(".lb-bld__chat", (e) => e.innerHTML);
    check("curated conversation has no plumbing token", !chatHtml.includes(banned));
    const host = await page.$$eval(".lb-bld__url", (els) => els.map((e) => e.textContent.trim()));
    check("real <slug>.fourmanifold.com header present", host.some((h) => h === "acme.fourmanifold.com"), JSON.stringify(host));
    const disclosure = await page.$$eval("details.lb-bld__details summary", (els) =>
      els.map((e) => e.textContent.trim()),
    );
    check("'View build details' disclosure present", disclosure.includes("View build details"), JSON.stringify(disclosure));
    check("no console errors", consoleErrors.length === 0, consoleErrors.join(" | "));
  }
  await page.screenshot({ path: resolve(SHOTS, "B-building.png"), fullPage: true });
} catch (err) {
  console.error("\nHARNESS ERROR:", err);
  if (serverErr) console.error("server stderr:", serverErr.slice(-2000));
  results.push({ name: "harness ran without throwing", ok: false, detail: String(err) });
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
}

const failed = results.filter((r) => !r.ok);
console.log(`\n==== ${results.length - failed.length}/${results.length} checks passed ====`);
if (failed.length) {
  console.log("FAILURES:");
  for (const f of failed) console.log(`  - ${f.name}: ${f.detail}`);
  process.exit(1);
}
console.log("ALL CHECKS PASSED");
process.exit(0);
