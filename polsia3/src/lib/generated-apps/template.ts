import fs from "node:fs/promises";
import path from "node:path";
import type { CompanyBuildInput } from "./records";

export type GeneratedAppTemplateConfig = {
  company: CompanyBuildInput;
  platformUrl: string;
  projectAiKey?: string | null;
};

function escapeText(value: string | undefined | null) {
  return JSON.stringify(value || "");
}

function productActionName(company: CompanyBuildInput) {
  const offer = company.offer || company.public_pitch || "Generate a useful plan";
  if (/audit|diagnos|review/i.test(offer)) return "Run audit";
  if (/plan|launch|strategy/i.test(offer)) return "Create plan";
  if (/convert|generate|write/i.test(offer)) return "Generate output";
  return "Run workflow";
}

export async function writeGeneratedAppTemplate(rootDir: string, config: GeneratedAppTemplateConfig) {
  await fs.rm(rootDir, { recursive: true, force: true });
  await fs.mkdir(path.join(rootDir, "src", "app", "api", "product", "run"), { recursive: true });
  await fs.mkdir(path.join(rootDir, "src", "app", "product"), { recursive: true });
  await fs.mkdir(path.join(rootDir, "src", "app", "signup"), { recursive: true });
  await fs.mkdir(path.join(rootDir, "src", "lib"), { recursive: true });
  await fs.mkdir(path.join(rootDir, "src", "product"), { recursive: true });

  const files = new Map<string, string>();
  files.set("package.json", packageJson(config));
  files.set("tsconfig.json", tsconfigJson());
  files.set("next-env.d.ts", nextEnv());
  files.set("next.config.ts", nextConfig());
  files.set("src/app/globals.css", globalsCss());
  files.set("src/app/layout.tsx", layoutTsx(config));
  files.set("src/app/page.tsx", homePageTsx(config));
  files.set("src/app/product/page.tsx", productPageTsx(config));
  files.set("src/app/signup/page.tsx", signupPageTsx(config));
  files.set("src/app/api/product/run/route.ts", productRunRoute(config));
  files.set("src/lib/platform-client.ts", platformClientTs(config));
  files.set("src/product/module.ts", productModuleTs(config));
  files.set(
    "takyon-manifest.json",
    `${JSON.stringify(
      {
        version: 1,
        companyId: config.company.id,
        slug: config.company.slug,
        generatedAt: new Date().toISOString(),
        files: Array.from(files.keys()).sort()
      },
      null,
      2
    )}\n`
  );

  for (const [relative, body] of files) {
    const absolute = path.join(rootDir, relative);
    await fs.mkdir(path.dirname(absolute), { recursive: true });
    await fs.writeFile(absolute, body, "utf8");
  }

  return {
    files: Array.from(files.keys()).sort(),
    rootDir
  };
}

function packageJson(config: GeneratedAppTemplateConfig) {
  return `${JSON.stringify(
    {
      name: `takyon-generated-${config.company.slug}`,
      private: true,
      version: "0.1.0",
      scripts: {
        build: "next build",
        start: "next start",
        typecheck: "tsc --noEmit",
        smoke: "tsx smoke.ts"
      },
      dependencies: {
        next: "^16.2.6",
        react: "^19.2.6",
        "react-dom": "^19.2.6",
        zod: "^4.4.3"
      },
      devDependencies: {
        "@types/node": "^24.10.1",
        "@types/react": "^19.2.15",
        "@types/react-dom": "^19.2.3",
        tsx: "^4.21.0",
        typescript: "^6.0.3"
      }
    },
    null,
    2
  )}\n`;
}

function tsconfigJson() {
  return `${JSON.stringify(
    {
      compilerOptions: {
        target: "ES2022",
        lib: ["dom", "dom.iterable", "es2022"],
        allowJs: false,
        skipLibCheck: true,
        strict: true,
        noEmit: true,
        esModuleInterop: true,
        module: "esnext",
        moduleResolution: "bundler",
        resolveJsonModule: true,
        isolatedModules: true,
        jsx: "react-jsx",
        incremental: true,
        plugins: [{ name: "next" }],
        paths: { "@/*": ["./src/*"] }
      },
      include: ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts", ".next/dev/types/**/*.ts"],
      exclude: ["node_modules"]
    },
    null,
    2
  )}\n`;
}

function nextEnv() {
  return `/// <reference types="next" />\n/// <reference types="next/image-types/global" />\n`;
}

function nextConfig() {
  return `import type { NextConfig } from "next";\n\nconst nextConfig: NextConfig = {\n  async headers() {\n    return [\n      {\n        source: "/:path*",\n        headers: [\n          {\n            key: "Content-Security-Policy",\n            value: "frame-ancestors 'self' https://app.fourmanifold.com https://fourmanifold.com http://localhost:3000 http://127.0.0.1:3000"\n          }\n        ]\n      }\n    ];\n  }\n};\n\nexport default nextConfig;\n`;
}

function layoutTsx(config: GeneratedAppTemplateConfig) {
  return `import type { Metadata } from "next";\nimport "./globals.css";\n\nexport const metadata: Metadata = {\n  title: ${escapeText(config.company.name)},\n  description: ${escapeText(config.company.public_pitch)}\n};\n\nexport default function RootLayout({ children }: { children: React.ReactNode }) {\n  return (\n    <html lang="en">\n      <body>{children}</body>\n    </html>\n  );\n}\n`;
}

function homePageTsx(config: GeneratedAppTemplateConfig) {
  return `import Link from "next/link";\nimport { productModule } from "@/product/module";\n\nconst businessName = ${escapeText(config.company.name)};\nconst pitch = ${escapeText(config.company.public_pitch || config.company.offer || "A focused product workflow for a real customer problem.")};\nconst customer = ${escapeText(config.company.customer || "busy operators")};\nconst pain = ${escapeText(config.company.pain || "manual work that should become a repeatable workflow")};\n\nexport default function HomePage() {\n  return (\n    <main className="surface-root">\n      <section className="hero-shell">\n        <nav className="topbar" aria-label="Main navigation">\n          <Link className="brand-lockup" href="/">\n            <span className="brand-mark" aria-hidden />\n            <span>{businessName}</span>\n          </Link>\n          <div className="nav-links">\n            <Link href="/product">Product</Link>\n            <Link href="/signup">Sign up</Link>\n          </div>\n        </nav>\n\n        <div className="hero-grid">\n          <section className="hero-copy">\n            <p className="eyebrow">{productModule.productName}</p>\n            <h1>{pitch}</h1>\n            <p className="hero-lede">\n              Built for {customer} who need a practical way through {pain} without waiting on a custom services project.\n            </p>\n            <div className="hero-actions">\n              <Link className="button primary" href="/signup">Start free</Link>\n              <Link className="button secondary" href="/product">{productModule.actionLabel}</Link>\n            </div>\n          </section>\n\n          <section className="workflow-panel" aria-label="Product preview">\n            <div className="panel-header">\n              <span>Workflow</span>\n              <strong>{productModule.actionLabel}</strong>\n            </div>\n            <ol className="workflow-steps">\n              <li><span>1</span><p>Share the situation, goal, and constraints.</p></li>\n              <li><span>2</span><p>Run the product workflow through the platform AI gateway.</p></li>\n              <li><span>3</span><p>Get a structured result with specific next actions.</p></li>\n            </ol>\n            <Link className="panel-link" href="/product">Open product</Link>\n          </section>\n        </div>\n      </section>\n\n      <section className="section-band">\n        <div className="section-grid three">\n          <article>\n            <h2>Focused input</h2>\n            <p>The product asks for the exact context needed to produce useful output.</p>\n          </article>\n          <article>\n            <h2>Account-ready</h2>\n            <p>Signup and checkout route through the platform auth and entitlement rails.</p>\n          </article>\n          <article>\n            <h2>Metered AI</h2>\n            <p>Usage is handled by the project AI gateway and business budget policy.</p>\n          </article>\n        </div>\n      </section>\n    </main>\n  );\n}\n`;
}

function productPageTsx(config: GeneratedAppTemplateConfig) {
  return `"use client";\n\nimport { FormEvent, useState } from "react";\nimport Link from "next/link";\nimport { productModule } from "@/product/module";\n\ntype ProductRunResult = {\n  ok?: boolean;\n  error?: string;\n  output?: unknown;\n  runId?: string;\n};\n\nfunction outputText(value: unknown) {\n  if (!value) return "";\n  if (typeof value === "string") return value;\n  return JSON.stringify(value, null, 2);\n}\n\nexport default function ProductPage() {\n  const [email, setEmail] = useState("");\n  const [brief, setBrief] = useState("");\n  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");\n  const [result, setResult] = useState<ProductRunResult | null>(null);\n\n  async function submit(event: FormEvent<HTMLFormElement>) {\n    event.preventDefault();\n    setStatus("running");\n    setResult(null);\n    try {\n      const response = await fetch("/api/product/run", {\n        method: "POST",\n        headers: { "content-type": "application/json" },\n        body: JSON.stringify({ email, brief })\n      });\n      const payload = (await response.json().catch(() => null)) as ProductRunResult | null;\n      if (!response.ok || !payload?.ok) throw new Error(payload?.error || "The product workflow could not run.");\n      setResult(payload);\n      setStatus("done");\n    } catch (error) {\n      setResult({ ok: false, error: error instanceof Error ? error.message : "The product workflow could not run." });\n      setStatus("error");\n    }\n  }\n\n  return (\n    <main className="surface-root product-page">\n      <nav className="topbar compact" aria-label="Product navigation">\n        <Link className="brand-lockup" href="/">\n          <span className="brand-mark" aria-hidden />\n          <span>{productModule.productName}</span>\n        </Link>\n        <div className="nav-links">\n          <Link href="/">Home</Link>\n          <Link href="/signup">Sign up</Link>\n        </div>\n      </nav>\n\n      <section className="product-layout">\n        <div className="product-copy">\n          <p className="eyebrow">Product workflow</p>\n          <h1>{productModule.actionLabel}</h1>\n          <p>{productModule.inputPlaceholder}</p>\n        </div>\n\n        <form className="product-form" onSubmit={submit}>\n          <label>\n            Work email\n            <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required placeholder="you@company.com" />\n          </label>\n          <label>\n            {productModule.inputLabel}\n            <textarea value={brief} onChange={(event) => setBrief(event.target.value)} required minLength={10} placeholder={productModule.inputPlaceholder} />\n          </label>\n          <button className="button primary" type="submit" disabled={status === "running"}>\n            {status === "running" ? "Running..." : productModule.actionLabel}\n          </button>\n        </form>\n\n        <section className="result-panel" aria-live="polite">\n          <div className="panel-header">\n            <span>{productModule.resultLabel}</span>\n            {result?.runId ? <strong>{result.runId.slice(0, 8)}</strong> : null}\n          </div>\n          {status === "idle" ? <p>Run the workflow to generate a structured result.</p> : null}\n          {status === "running" ? <p>Processing through the product runtime.</p> : null}\n          {status === "error" ? <p className="error-text">{result?.error}</p> : null}\n          {status === "done" ? <pre>{outputText(result?.output)}</pre> : null}\n        </section>\n      </section>\n    </main>\n  );\n}\n`;
}

function signupPageTsx(config: GeneratedAppTemplateConfig) {
  return `"use client";\n\nimport { FormEvent, useState } from "react";\nimport Link from "next/link";\nimport { generatedAppCheckoutUrl, requestMagicLink } from "@/lib/platform-client";\n\nconst businessName = ${escapeText(config.company.name)};\n\nexport default function SignupPage() {\n  const [email, setEmail] = useState("");\n  const [message, setMessage] = useState<string | null>(null);\n  const [error, setError] = useState<string | null>(null);\n  const [busy, setBusy] = useState(false);\n\n  async function submit(event: FormEvent<HTMLFormElement>) {\n    event.preventDefault();\n    setBusy(true);\n    setError(null);\n    setMessage(null);\n    try {\n      await requestMagicLink(email);\n      setMessage("Check your email for the sign-in link.");\n    } catch (caught) {\n      setError(caught instanceof Error ? caught.message : "Signup is not available right now.");\n    } finally {\n      setBusy(false);\n    }\n  }\n\n  return (\n    <main className="surface-root signup-page">\n      <nav className="topbar compact" aria-label="Signup navigation">\n        <Link className="brand-lockup" href="/">\n          <span className="brand-mark" aria-hidden />\n          <span>{businessName}</span>\n        </Link>\n        <div className="nav-links">\n          <Link href="/">Home</Link>\n          <Link href="/product">Product</Link>\n        </div>\n      </nav>\n\n      <section className="signup-layout">\n        <div className="signup-copy">\n          <p className="eyebrow">Account</p>\n          <h1>Start using {businessName}</h1>\n          <p>Create an account with a magic link, then upgrade when you need the paid workflow allowance.</p>\n        </div>\n\n        <form className="signup-form" onSubmit={submit}>\n          <label>\n            Email\n            <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required placeholder="you@company.com" />\n          </label>\n          <button className="button primary" type="submit" disabled={busy}>{busy ? "Sending..." : "Send magic link"}</button>\n          {message ? <p className="success-text">{message}</p> : null}\n          {error ? <p className="error-text">{error}</p> : null}\n        </form>\n\n        <div className="pricing-grid">\n          <article>\n            <h2>Free</h2>\n            <p>Try the workflow with limited usage.</p>\n            <Link className="button secondary" href="/product">Try product</Link>\n          </article>\n          <article>\n            <h2>Starter</h2>\n            <p>Upgrade for a paid monthly allowance and checkout-backed access.</p>\n            <a className="button primary" href={generatedAppCheckoutUrl("starter")}>Upgrade</a>\n          </article>\n        </div>\n      </section>\n    </main>\n  );\n}\n`;
}

function productRunRoute(config: GeneratedAppTemplateConfig) {
  return `import { z } from "zod";\nimport { runProductWorkflow } from "@/lib/platform-client";\nimport { productModule } from "@/product/module";\n\nconst schema = z.object({\n  email: z.string().email(),\n  brief: z.string().trim().min(10).max(4000)\n});\n\nexport async function POST(request: Request) {\n  const parsed = schema.safeParse(await request.json());\n  if (!parsed.success) {\n    return Response.json({ ok: false, error: "Enter a valid email and brief." }, { status: 400 });\n  }\n\n  const result = await runProductWorkflow({\n    companyId: ${escapeText(config.company.id)},\n    route: "/product",\n    purpose: "product",\n    module: productModule,\n    input: parsed.data\n  });\n\n  return Response.json(result, { status: result.ok ? 200 : 424 });\n}\n`;
}

function platformClientTs(config: GeneratedAppTemplateConfig) {
  return `const platformUrl = process.env.TAKYON_PLATFORM_URL || ${escapeText(config.platformUrl)};\nconst projectKey = process.env.ARGON_PROJECT_AI_KEY || ${escapeText(config.projectAiKey || "")};\nconst siteSlug = ${escapeText(config.company.slug)};\n\ntype ProductModule = {\n  productName?: string;\n  category?: string;\n  actionLabel?: string;\n  inputLabel?: string;\n  inputPlaceholder?: string;\n  resultLabel?: string;\n  systemPrompt?: string;\n  outputInstructions?: string;\n};\n\ntype ProductRunInput = {\n  companyId: string;\n  route: string;\n  purpose: string;\n  module?: ProductModule;\n  input: { email: string; brief: string };\n};\n\nexport function generatedAppCheckoutUrl(planKey = "starter") {\n  const url = new URL(\`/api/generated-apps/\${siteSlug}/checkout\`, platformUrl);\n  url.searchParams.set("plan", planKey);\n  return url.toString();\n}\n\nexport async function requestMagicLink(email: string) {\n  const response = await fetch(new URL(\`/api/generated-apps/\${siteSlug}/auth/request\`, platformUrl), {\n    method: "POST",\n    headers: { "content-type": "application/json" },\n    body: JSON.stringify({ email })\n  });\n  const payload = await response.json().catch(() => null) as { ok?: boolean; error?: string } | null;\n  if (!response.ok || !payload?.ok) {\n    throw new Error(payload?.error || "Sign-up is not available right now.");\n  }\n  return payload;\n}\n\nexport async function loadAppSession() {\n  const response = await fetch(new URL(\`/api/generated-apps/\${siteSlug}/session\`, platformUrl), { cache: "no-store" });\n  return response.json();\n}\n\nexport async function runProductWorkflow(input: ProductRunInput) {\n  if (!projectKey) {\n    return { ok: false, error: "Project access is not configured for this app." };\n  }\n\n  const response = await fetch(new URL("/api/generated-apps/runtime/product-runs", platformUrl), {\n    method: "POST",\n    headers: {\n      "authorization": \`Bearer \${projectKey}\`,\n      "content-type": "application/json"\n    },\n    body: JSON.stringify(input)\n  });\n\n  return response.json();\n}\n`;
}

function productModuleTs(config: GeneratedAppTemplateConfig) {
  return `export const productModule = {\n  productName: ${escapeText(config.company.name)},\n  category: "product",\n  actionLabel: ${escapeText(productActionName(config.company))},\n  inputLabel: "Describe what you need",\n  inputPlaceholder: ${escapeText(config.company.offer || config.company.public_pitch || "Share the work you want handled.")},\n  resultLabel: "Result",\n  systemPrompt: "Act as the customer-facing product workflow for this generated app. Use the visitor input and company context to produce useful, specific output. Do not mention internal build systems, infrastructure, vendors, or implementation state.",\n  outputInstructions: "Return JSON with summary and exactly three nextSteps."\n} as const;\n`;
}

function globalsCss() {
  return `*{box-sizing:border-box}html{background:#f6f3ee;color:#171716}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.surface-root{min-height:100vh}.hero-shell{min-height:82vh;padding:24px clamp(20px,4vw,64px) 56px;background:linear-gradient(135deg,#f9f7f2 0%,#e9f0ea 46%,#f2ece1 100%)}.topbar{display:flex;align-items:center;justify-content:space-between;gap:20px;max-width:1160px;margin:0 auto 72px}.topbar.compact{padding:24px clamp(20px,4vw,64px);margin:0 auto;max-width:1160px}.brand-lockup{display:inline-flex;align-items:center;gap:10px;color:#171716;text-decoration:none;font-weight:800}.brand-mark{width:14px;height:14px;border-radius:4px;background:#147d64;box-shadow:8px 8px 0 #d6572a}.nav-links{display:flex;gap:18px;align-items:center}.nav-links a{color:#3f4640;text-decoration:none;font-size:14px;font-weight:700}.hero-grid,.product-layout,.signup-layout{max-width:1160px;margin:0 auto;display:grid;grid-template-columns:minmax(0,1.1fr) minmax(320px,.9fr);gap:36px;align-items:center}.hero-copy h1,.product-copy h1,.signup-copy h1{font-size:clamp(42px,7vw,82px);line-height:.96;margin:0 0 24px;letter-spacing:0;color:#171716}.hero-lede,.product-copy p,.signup-copy p{font-size:18px;line-height:1.6;color:#454b46;max-width:720px}.eyebrow{margin:0 0 16px;text-transform:uppercase;letter-spacing:.08em;font-size:12px;font-weight:900;color:#147d64}.hero-actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:32px}.button{display:inline-flex;min-height:46px;align-items:center;justify-content:center;border-radius:8px;padding:0 18px;border:1px solid #171716;text-decoration:none;font-weight:900;cursor:pointer}.button.primary{background:#171716;color:#fff}.button.secondary{background:#fff;color:#171716}.button:disabled{opacity:.6;cursor:not-allowed}.workflow-panel,.result-panel,.product-form,.signup-form,.pricing-grid article{border:1px solid #d6d0c6;background:rgba(255,255,255,.78);border-radius:8px;padding:24px;box-shadow:0 20px 60px rgba(23,23,22,.08)}.panel-header{display:flex;justify-content:space-between;gap:12px;margin-bottom:18px;color:#60665f;font-size:13px;text-transform:uppercase;letter-spacing:.06em}.panel-header strong{color:#171716}.workflow-steps{list-style:none;margin:0;padding:0;display:grid;gap:14px}.workflow-steps li{display:grid;grid-template-columns:34px 1fr;gap:12px;align-items:start}.workflow-steps span{display:grid;place-items:center;width:34px;height:34px;border-radius:8px;background:#147d64;color:white;font-weight:900}.workflow-steps p{margin:6px 0 0;color:#454b46;line-height:1.5}.panel-link{display:inline-flex;margin-top:22px;color:#147d64;font-weight:900}.section-band{padding:56px clamp(20px,4vw,64px);background:#171716;color:#fff}.section-grid{max-width:1160px;margin:0 auto;display:grid;gap:20px}.section-grid.three{grid-template-columns:repeat(3,minmax(0,1fr))}.section-grid article{border-top:1px solid rgba(255,255,255,.22);padding-top:18px}.section-grid h2{margin:0 0 10px;font-size:20px}.section-grid p{margin:0;color:#d8d4ca;line-height:1.55}.product-page,.signup-page{background:#f6f3ee}.product-layout,.signup-layout{padding:56px clamp(20px,4vw,64px) 80px;align-items:start}.product-form,.signup-form{display:grid;gap:16px}.product-form label,.signup-form label{display:grid;gap:8px;font-size:14px;font-weight:900;color:#30342f}input,textarea{width:100%;border:1px solid #c8c1b4;background:#fff;border-radius:8px;padding:13px 14px;font:inherit;color:#171716}textarea{min-height:180px;resize:vertical}.result-panel{min-height:280px;overflow:auto}.result-panel p{color:#555b55;line-height:1.55}.result-panel pre{white-space:pre-wrap;margin:0;font:14px/1.6 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;color:#171716}.error-text{color:#9b1c1c}.success-text{color:#147d64}.pricing-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;background:transparent;border:0;box-shadow:none;padding:0}.pricing-grid h2{margin:0 0 8px}.pricing-grid p{color:#555b55;line-height:1.5}@media (max-width:820px){.topbar{margin-bottom:44px}.hero-grid,.product-layout,.signup-layout,.section-grid.three{grid-template-columns:1fr}.hero-copy h1,.product-copy h1,.signup-copy h1{font-size:42px}.pricing-grid{grid-template-columns:1fr}.nav-links{gap:12px}.hero-shell{min-height:auto}}`;
}
