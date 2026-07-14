import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { BackButton } from "../components/back-button";
import { PublicSiteHeader } from "../components/site-navigation";
import { Badge } from "../components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { businessDisplayName } from "../lib/branding";
import { resolveViewerCta, useViewerAccess } from "../lib/hooks";
import { useProductAuth } from "../lib/product-auth";
import { defaultPlanLimitLabels, defaultPlanPriceLabel } from "../lib/takyon";

const faqItems = [
  {
    question: "How do I access the app after subscribing?",
    answer: "Log in with the same Google account you used at checkout. Active subscribers go directly to the product.",
  },
  {
    question: "What happens if my subscription is past due or canceled?",
    answer: "Access pauses until billing is updated. Log in to follow the secure billing path.",
  },
  {
    question: "What data does the app store about me?",
    answer: "The product stores the account, product records, and usage receipts needed to provide the service.",
  },
];

const privacySections = [
  { title: "Information we collect", body: "We can collect account identity, subscription state, profile details, product records, and usage receipts needed to operate the service." },
  { title: "How information is used", body: "We use this data to sign you in, manage access, process billing events, and deliver the features you request." },
  { title: "Payments", body: "Subscription billing and payment events are handled by secure payment processors." },
  { title: "Retention and deletion", body: "Operational records may be retained for reliability, billing, and abuse prevention. Contact support for deletion instructions." },
];

const termsSections = [
  { title: "Using the service", body: "Use the product only for its stated purpose and in line with its acceptable-use rules." },
  { title: "Accounts and subscriptions", body: "Some features require an active account and paid entitlement. Renewal and cancellation follow the published checkout terms." },
  { title: "Product-specific rules", body: "Additional rules may cover warranties, liability, governing law, and regulated uses." },
];

function SupportLayout({ eyebrow, title, description, children }: { eyebrow: string; title: string; description: string; children: ReactNode }) {
  const access = useViewerAccess();
  return (
    <main className="min-h-screen bg-background">
      <PublicSiteHeader access={access} />
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-6 py-10 sm:px-8 sm:py-14">
        <BackButton />
        <header className="flex flex-col gap-4 border-b border-border pb-8">
          <Badge variant="outline" className="w-fit">{eyebrow}</Badge>
          <h1 className="font-heading text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">{title}</h1>
          <p className="max-w-3xl text-base leading-7 text-muted-foreground sm:text-lg">{description}</p>
        </header>
        {children}
      </div>
    </main>
  );
}

export function PricingScreen() {
  const access = useViewerAccess();
  const auth = useProductAuth();
  const cta = resolveViewerCta(access);
  const productName = businessDisplayName();
  const price = defaultPlanPriceLabel();
  const limits = defaultPlanLimitLabels();

  return (
    <main className="min-h-screen bg-background">
      <PublicSiteHeader access={access} />
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-6 py-10 sm:py-14">
        <BackButton />
        <section className="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_24rem]">
          <div className="space-y-4">
            <Badge variant="outline">Pricing</Badge>
            <h1 className="font-heading text-5xl font-semibold tracking-tight text-foreground">One plan. The complete {productName} experience.</h1>
            <p className="text-lg leading-8 text-muted-foreground">Create your account, complete secure checkout, and go directly into the product.</p>
          </div>
          <Card className="shadow-xl">
            <CardHeader>
              <CardTitle>{price || "Monthly subscription"}</CardTitle>
              <CardDescription>Everything required to use the product.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3">
              <div className="rounded-lg border border-border bg-muted/40 p-4">
                <p className="text-sm font-semibold text-foreground">Plan limits</p>
                {limits.length ? (
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                    {limits.map((limit) => <li key={limit}>{limit}</li>)}
                  </ul>
                ) : (
                  <p className="mt-2 text-sm text-muted-foreground">No numeric usage limit is configured for this plan.</p>
                )}
              </div>
              {access.loading ? (
                <div className="h-12 animate-pulse rounded bg-muted" />
              ) : access.authenticated ? (
                <Link to={cta.primaryHref} className="inline-flex h-12 items-center justify-center rounded bg-primary px-6 font-medium text-primary-foreground hover:opacity-90">
                  {cta.primaryLabel}
                </Link>
              ) : (
                <>
                  <button type="button" onClick={() => void auth.signUpWithGoogle()} className="h-12 rounded bg-primary px-6 font-medium text-primary-foreground hover:opacity-90">Sign up</button>
                  <button type="button" onClick={() => void auth.signInWithGoogle()} className="h-12 rounded border border-border bg-background px-6 font-medium text-foreground hover:bg-muted">Log in</button>
                </>
              )}
            </CardContent>
          </Card>
        </section>
      </div>
    </main>
  );
}

export function FaqScreen() {
  return <SupportLayout eyebrow="FAQ" title="Frequently asked questions" description="Straight answers about account, billing, and access."><section className="grid gap-4">{faqItems.map((item) => <Card key={item.question}><CardHeader><CardTitle>{item.question}</CardTitle></CardHeader><CardContent><p className="text-sm leading-7 text-muted-foreground">{item.answer}</p></CardContent></Card>)}</section></SupportLayout>;
}

export function PrivacyScreen() {
  return <SupportLayout eyebrow="Privacy" title="Privacy policy" description="How we handle the data needed to run your account and product."><section className="grid gap-4">{privacySections.map((section) => <Card key={section.title}><CardHeader><CardTitle>{section.title}</CardTitle></CardHeader><CardContent><p className="text-sm leading-7 text-muted-foreground">{section.body}</p></CardContent></Card>)}</section></SupportLayout>;
}

export function TermsScreen() {
  return <SupportLayout eyebrow="Terms" title="Terms of service" description="The terms that govern your use of this product."><section className="grid gap-4">{termsSections.map((section) => <Card key={section.title}><CardHeader><CardTitle>{section.title}</CardTitle></CardHeader><CardContent><p className="text-sm leading-7 text-muted-foreground">{section.body}</p></CardContent></Card>)}</section></SupportLayout>;
}

export function ArticlesScreen() {
  return <SupportLayout eyebrow="Articles" title="Guides and updates" description="Product guides, release notes, and support articles."><Card><CardHeader><CardTitle>No articles published yet</CardTitle><CardDescription>Guides will appear here as the product grows.</CardDescription></CardHeader><CardContent><Link to="/faq" className="inline-flex rounded border border-border bg-card px-4 py-2 text-sm font-medium text-foreground hover:bg-muted">Browse FAQ</Link></CardContent></Card></SupportLayout>;
}
