import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Badge } from "../components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";

const supportLinks = [
  { href: "/", label: "Home" },
  { href: "/faq", label: "FAQ" },
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
  { href: "/articles", label: "Articles" },
  { href: "/app", label: "Open app" },
];

const faqItems = [
  {
    question: "How do I access the app after subscribing?",
    answer:
      "Use the same email you used during checkout or sign-in. Once your session is active, the app can read your account and subscription state automatically.",
  },
  {
    question: "What happens if my subscription is past due or canceled?",
    answer:
      "Your access may pause until billing is updated. The account page is the canonical place to check entitlement state, renew access, or manage cancellation.",
  },
  {
    question: "What data does the app store about me?",
    answer:
      "The product can store the account details, profile fields, app records, and usage receipts needed to run the service you signed up for. Businesses should customize the final policy for their specific data model.",
  },
  {
    question: "How do I get help?",
    answer:
      "Start with the account or product experience inside the app. If the business offers direct support, this page is the right place to add contact details and response expectations.",
  },
];

const privacySections = [
  {
    title: "Information the product may collect",
    body:
      "A Takyon-powered app can collect account identity, subscription state, profile details you submit, product records you create, and usage receipts needed to operate the service.",
  },
  {
    title: "How that information is used",
    body:
      "The shared runtime uses account and session data to authenticate you, enforce entitlements, process checkout events, meter usage where applicable, and deliver the product features you requested.",
  },
  {
    title: "Payments and processors",
    body:
      "Subscription billing and payment events may be handled by external payment processors. Businesses should publish their exact processor list, support email, and refund policy before shipping live traffic.",
  },
  {
    title: "Retention and deletion",
    body:
      "Operational logs and receipts may be retained for reliability, billing, and abuse prevention. Each business should replace this starter copy with exact retention windows and deletion instructions.",
  },
];

const termsSections = [
  {
    title: "Using the service",
    body:
      "You may use the product only in line with its stated purpose, pricing, and acceptable use rules. Access may be limited or revoked for abuse, fraud, or attempts to bypass account or billing controls.",
  },
  {
    title: "Accounts and subscriptions",
    body:
      "Some features require an active account or paid entitlement. Subscription status, trial access, renewal, and cancellation are governed by the business's published plan policy and checkout terms.",
  },
  {
    title: "Business-specific rules",
    body:
      "This is starter legal copy. The business should replace it with product-specific terms covering warranties, liability limits, governing law, and any regulated-use restrictions before public launch.",
  },
];

function SupportLayout({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <main className="min-h-screen bg-background" data-takyon-scaffold="support">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-10 px-6 py-12 sm:px-8 sm:py-16">
        <header className="flex flex-col gap-6">
          <div className="flex flex-wrap items-center gap-3">
            <Badge variant="outline">{eyebrow}</Badge>
            <span className="text-sm text-muted-foreground">Starter public support page</span>
          </div>
          <div className="flex flex-col gap-4">
            <h1 className="font-heading text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
              {title}
            </h1>
            <p className="max-w-3xl text-base leading-7 text-muted-foreground sm:text-lg">
              {description}
            </p>
          </div>
          <nav aria-label="Support pages" className="flex flex-wrap gap-3">
            {supportLinks.map((link) => (
              <Link
                key={link.href}
                to={link.href}
                className="rounded border border-border bg-card px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted"
              >
                {link.label}
              </Link>
            ))}
          </nav>
        </header>
        {children}
      </div>
    </main>
  );
}

export function FaqScreen() {
  return (
    <SupportLayout
      eyebrow="FAQ"
      title="Frequently asked questions"
      description="This starter page answers the most common account, billing, and access questions. Replace or extend the answers with business-specific support details before launch."
    >
      <section className="grid gap-4">
        {faqItems.map((item) => (
          <Card key={item.question}>
            <CardHeader>
              <CardTitle>{item.question}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-7 text-muted-foreground">{item.answer}</p>
            </CardContent>
          </Card>
        ))}
      </section>
    </SupportLayout>
  );
}

export function PrivacyScreen() {
  return (
    <SupportLayout
      eyebrow="Privacy"
      title="Privacy policy"
      description="This shared starter explains the kinds of data a subscription app commonly needs to run. Businesses should customize the final live policy with exact data handling and contact details."
    >
      <section className="grid gap-4">
        {privacySections.map((section) => (
          <Card key={section.title}>
            <CardHeader>
              <CardTitle>{section.title}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-7 text-muted-foreground">{section.body}</p>
            </CardContent>
          </Card>
        ))}
      </section>
    </SupportLayout>
  );
}

export function TermsScreen() {
  return (
    <SupportLayout
      eyebrow="Terms"
      title="Terms of service"
      description="This seeded terms page keeps the route truthful and non-empty during bootstrap. It should be replaced with business-specific legal terms before a live launch."
    >
      <section className="grid gap-4">
        {termsSections.map((section) => (
          <Card key={section.title}>
            <CardHeader>
              <CardTitle>{section.title}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-7 text-muted-foreground">{section.body}</p>
            </CardContent>
          </Card>
        ))}
      </section>
    </SupportLayout>
  );
}

export function ArticlesScreen() {
  return (
    <SupportLayout
      eyebrow="Articles"
      title="Guides and updates"
      description="This route is ready for product education, changelog posts, and support articles. It now renders a real starter state instead of a blank page."
    >
      <Card>
        <CardHeader>
          <CardTitle>No articles published yet</CardTitle>
          <CardDescription>
            Add product guides, onboarding help, and release notes here as the business grows.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <Link
            to="/faq"
            className="rounded border border-border bg-card px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted"
          >
            Browse FAQ
          </Link>
          <Link
            to="/app"
            className="rounded border border-border bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >
            Open app
          </Link>
        </CardContent>
      </Card>
    </SupportLayout>
  );
}
