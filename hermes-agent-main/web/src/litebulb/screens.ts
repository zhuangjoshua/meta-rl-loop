/* ============================================================================
   SCREEN REGISTRY — the one place that maps every screen to its route, auth
   requirement, and the backend it needs wired. Keep in sync with the routes in
   App.tsx and the header label in each screen file. The backend agent reads
   THIS to know what to hook up where.
   ============================================================================ */

export type AuthReq = "public" | "authed";

export interface ScreenDef {
  name: string;
  route: string;   // eventual clean path (what the real app should use)
  hash: string;    // mock hash route used in this prototype
  auth: AuthReq;
  file: string;
  hooks: string[]; // backend integrations this screen needs
  note?: string;
}

export const SCREENS: ScreenDef[] = [
  // ── PUBLIC (logged-out / marketing) ──
  { name: "Home (marketing)", route: "/", hash: "#/", auth: "public", file: "src/landing/Landing.tsx",
    hooks: ["Auth0: gate 'Start building' → open Auth modal"] },
  { name: "Pricing (public)", route: "/pricing", hash: "#/pricing", auth: "public", file: "src/litebulb/marketing/Pricing.tsx",
    hooks: ["GET /api/takyon/public/operator/plans (config-driven tiers, no free tier)", "Auth0: CTA → Auth modal → Settings ▸ Plans checkout"] },
  { name: "FAQ / Help", route: "/faq", hash: "#/faq", auth: "public", file: "src/marketing/Faq.tsx", hooks: [] },
  { name: "Legal", route: "/terms,/privacy", hash: "#/terms", auth: "public", file: "src/marketing/Legal.tsx", hooks: [] },

  // ── AUTH (Auth0) ──
  { name: "Auth modal", route: "(overlay)", hash: "(overlay)", auth: "public", file: "src/auth/AuthModal.tsx",
    hooks: ["Auth0: Universal Login / social + email", "on success → session → /"], note: "Sign up / Log in tabs" },

  // ── APP (logged-in) ──
  { name: "Home (app)", route: "/", hash: "#/", auth: "authed", file: "src/app/AppHome.tsx",
    hooks: ["GET /companies", "POST /companies (prompt → create)"], note: "auth-aware: same URL as marketing home" },
  { name: "New company", route: "/app/new", hash: "#/app/new", auth: "authed", file: "src/app/NewCompany.tsx",
    hooks: ["POST /companies"], note: "focused prompt; swappable to a modal" },
  { name: "Company workspace", route: "/app/c/:id", hash: "#/build", auth: "authed", file: "src/product/Product.tsx",
    hooks: ["GET /companies/:id", "company build/activity APIs", "chat → agent API"] },
  { name: "Settings · Profile", route: "/settings/profile", hash: "#/settings/profile", auth: "authed", file: "src/settings/Profile.tsx",
    hooks: ["Auth0: profile, email, password reset, connected accounts"] },
  { name: "Settings · Billing", route: "/settings/billing", hash: "#/settings/billing", auth: "authed", file: "src/settings/Billing.tsx",
    hooks: ["Stripe: current plan + usage", "Stripe Customer Portal (manage / invoices / payment method)"] },
  { name: "Settings · Plans", route: "/settings/plans", hash: "#/settings/plans", auth: "authed", file: "src/litebulb/settings/Settings.tsx",
    hooks: ["GET /api/takyon/operator/billing/plans (config-driven tier catalog)", "Stripe Checkout: select tier → POST /api/takyon/operator/billing/checkout → redirect"],
    note: "Rendered as the 'Plans' tab in the Settings modal (multi-tier, no free tier)" },
  { name: "Settings · Referrals", route: "/settings/referrals", hash: "#/settings/referrals", auth: "authed", file: "src/settings/Referrals.tsx",
    hooks: ["referral code API"] },
  { name: "Settings · Company", route: "/settings/company/:id", hash: "#/settings/company", auth: "authed", file: "src/settings/CompanySettings.tsx",
    hooks: ["PATCH /companies/:id", "DELETE /companies/:id (danger zone)"] },
  { name: "Download code", route: "(overlay)", hash: "(overlay)", auth: "authed", file: "src/product/DownloadCodeModal.tsx",
    hooks: ["export API (zip)", "GitHub export"] },
  { name: "404 / error", route: "*", hash: "(fallback)", auth: "public", file: "src/common/NotFound.tsx", hooks: [] },
];
