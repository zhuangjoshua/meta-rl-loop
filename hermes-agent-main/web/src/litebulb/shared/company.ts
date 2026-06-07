/* Turn a free-text idea into a plausible seeded company for the workspace.
   Pure + deterministic (no randomness) so the same idea always yields the
   same demo company — this is a mockup; the real naming/plan comes from the
   backend later. */

export interface BuildStep {
  id: string;
  label: string;
  detail: string;
  status: "done" | "active" | "queued";
}

export interface Company {
  name: string;
  idea: string;
  tagline: string;
  audience: string;
  channels: string[];
  steps: BuildStep[];
}

/* A few hand-tuned presets so the suggestion pills demo really well. */
const PRESETS: Record<string, Omit<Company, "idea">> = {
  receipt: {
    name: "Receiptly",
    tagline: "Turn receipts into clean expense reports",
    audience: "Freelancers who hate bookkeeping",
    channels: ["SEO — expense guides", "Reddit + X", "Accountant partners"],
    steps: [],
  },
  resume: {
    name: "Résumé",
    tagline: "AI resume builder that lands interviews",
    audience: "Job-seekers switching careers",
    channels: ["SEO — 60 role pages", "TikTok how-tos", "LinkedIn outreach"],
    steps: [],
  },
  events: {
    name: "Hyperlocal",
    tagline: "What's happening near you, tonight",
    audience: "City dwellers, 22–35",
    channels: ["Local SEO", "Instagram Reels", "Campus ambassadors"],
    steps: [],
  },
  newsletter: {
    name: "Niche.fm",
    tagline: "A profitable newsletter in any niche",
    audience: "Domain experts with an audience",
    channels: ["X threads", "Referral loop", "Reddit communities"],
    steps: [],
  },
  shopify: {
    name: "Shoplift",
    tagline: "The Shopify plugin merchants ask for",
    audience: "DTC store owners on Shopify",
    channels: ["App Store listing", "Agency partners", "YouTube reviews"],
    steps: [],
  },
  habit: {
    name: "Streaky",
    tagline: "The habit tracker that actually sticks",
    audience: "Self-improvement crowd",
    channels: ["ASO", "Reddit r/getdisciplined", "Creator seeding"],
    steps: [],
  },
};

const KEYS: Array<[RegExp, keyof typeof PRESETS]> = [
  [/receipt|expense|bookkeep|invoice|accounting/i, "receipt"],
  [/resume|cv|cover letter|job/i, "resume"],
  [/event|local|tonight|near you|meetup/i, "events"],
  [/newsletter|substack|email list|digest/i, "newsletter"],
  [/shopify|ecommerce|merch|store|dtc/i, "shopify"],
  [/habit|streak|routine|fitness|track/i, "habit"],
];

const TITLE = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/* Coin a name from the first meaningful word when no preset matches. */
function coinName(idea: string): string {
  const stop = new Set(["a", "an", "the", "app", "for", "that", "to", "my", "with", "of", "and"]);
  const word =
    idea
      .toLowerCase()
      .replace(/[^a-z\s]/g, " ")
      .split(/\s+/)
      .find((w) => w.length > 3 && !stop.has(w)) || "lumen";
  const root = TITLE(word);
  if (/[aeiouy]$/i.test(root)) return root + "o";
  if (/(er|or|ly|fy|ify|io|hq)$/i.test(root)) return root;
  return root + "ly";
}

export function deriveCompany(rawIdea: string): Company {
  const idea = rawIdea.trim() || "An app that turns receipts into clean expense reports for freelancers";

  const hit = KEYS.find(([re]) => re.test(idea));
  const base = hit
    ? PRESETS[hit[1]]
    : {
        name: coinName(idea),
        tagline: idea.length > 64 ? idea.slice(0, 61).trimEnd() + "…" : idea,
        audience: "Early adopters who feel this pain",
        channels: ["Programmatic SEO", "Reddit + X", "Founder-led content"],
      };

  const steps: BuildStep[] = [
    { id: "scope", label: "Scope & positioning", detail: "Researched market, named the company, picked the wedge", status: "done" },
    { id: "brand", label: "Brand & landing page", detail: "Logo, copy, and a live waitlist page", status: "done" },
    { id: "mvp", label: "Build the MVP", detail: "Core flow shipping to production", status: "active" },
    { id: "launch", label: "Launch campaigns", detail: "Spinning up the first 3 acquisition channels", status: "active" },
    { id: "grow", label: "Growth loop", detail: "Referral + retention experiments", status: "queued" },
  ];

  return { idea, name: base.name, tagline: base.tagline, audience: base.audience, channels: base.channels, steps };
}
