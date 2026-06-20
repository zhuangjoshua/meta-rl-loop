"""Deterministic instant first-paint landing.

The bootstrap's full design pass (Sonnet) takes ~280s, so a customer waits minutes for
their first paint. This module renders a real, designed, BRANDED landing deterministically
(no model) from the step-1 brief, so it can publish in ~2 minutes — BEFORE the design pass
replaces it with the bespoke version.

Why files, not data: the publish gate (core._scaffold_visible_shell_unfinished_blocker +
_scaffold_theme_unfinished_blocker) refuses to publish while `landing.tsx` is byte-identical
to the scaffold and `tokens.css` is the placeholder. So the instant landing writes a real,
different `src/screens/landing.tsx` and a themed `src/tokens.css`; both pass the gate.

The rendered TSX uses ONLY the scaffold's existing imports (Button, branding, hooks,
product-auth) so it compiles against the pinned scaffold with no new deps.
"""

from __future__ import annotations

import html
import json
import re

# Carefully escaped so the rendered file is valid TSX and never byte-identical to the scaffold
# (so the visible-shell gate passes) and carries NO `data-takyon-scaffold` sentinel.
_LANDING_TEMPLATE = '''import { Link } from "react-router-dom";
import { Button } from "../components/ui/button";
import { brandMarkDataUri, businessDisplayName } from "../lib/branding";
import { resolveViewerCta, useViewerAccess } from "../lib/hooks";
import { useProductAuth } from "../lib/product-auth";

// Instant branded first-paint, rendered deterministically from the launch brief. The full
// design pass replaces this with the bespoke landing; this exists so the customer sees a real,
// on-message page within the first couple of minutes.
const EYEBROW = __EYEBROW__;
const HEADLINE = __HEADLINE__;
const SUBHEAD = __SUBHEAD__;
const PRIMARY_CTA = __PRIMARY_CTA__;
const FEATURES: Array<{ title: string; body: string }> = __FEATURES__;

export function LandingScreen() {
  const access = useViewerAccess();
  const auth = useProductAuth();
  const productName = businessDisplayName();
  const cta = resolveViewerCta(access);

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-16 px-6 py-12 sm:py-16">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <img
              src={brandMarkDataUri()}
              alt={`${productName} logo`}
              className="h-10 w-10 rounded-lg"
              width={40}
              height={40}
            />
            <span className="font-heading text-lg font-semibold">{productName}</span>
          </div>
          <nav className="flex flex-wrap gap-2">
            {[
              { to: "/faq", label: "FAQ" },
              { to: "/privacy", label: "Privacy" },
              { to: "/terms", label: "Terms" },
              { to: "/app", label: "Open app" },
            ].map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className="rounded border border-border bg-card px-3 py-2 text-sm font-medium transition-colors hover:bg-muted"
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </header>

        <section className="grid items-center gap-10 lg:grid-cols-[1.15fr_1fr]">
          <div className="flex flex-col gap-6">
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-primary">{EYEBROW}</p>
            <h1 className="font-heading text-4xl font-semibold tracking-tight sm:text-5xl">{HEADLINE}</h1>
            <p className="max-w-2xl text-lg leading-8 text-muted-foreground">{SUBHEAD}</p>
            <div className="flex flex-wrap gap-3">
              {access.authenticated ? (
                <Link
                  to={cta.primaryHref}
                  className="inline-flex h-12 items-center justify-center rounded bg-primary px-6 text-base font-medium text-primary-foreground transition-opacity hover:opacity-90"
                >
                  {cta.primaryLabel}
                </Link>
              ) : (
                <Button
                  size="lg"
                  onClick={() => void auth.signInWithGoogle()}
                  disabled={!auth.available || !auth.configured || auth.busy || access.authenticated}
                >
                  {auth.busy ? "Signing you in…" : PRIMARY_CTA}
                </Button>
              )}
              <Link
                to="/faq"
                className="inline-flex h-12 items-center justify-center rounded border border-border bg-card px-6 text-base font-medium transition-colors hover:bg-muted"
              >
                Learn more
              </Link>
            </div>
            {!auth.available || !auth.configured ? (
              <span className="text-sm text-muted-foreground">Sign-in is temporarily unavailable. Please try again shortly.</span>
            ) : null}
            {auth.error ? <span className="text-sm text-destructive">{auth.error}</span> : null}
          </div>

          <div className="grid gap-4 rounded-2xl border border-border bg-card p-6">
            {FEATURES.map((feature) => (
              <div key={feature.title} className="flex flex-col gap-1 border-b border-border pb-4 last:border-b-0 last:pb-0">
                <span className="font-heading text-base font-semibold">{feature.title}</span>
                <span className="text-sm leading-6 text-muted-foreground">{feature.body}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
'''

# A clean, neutral light theme keyed off one brand accent. Replaces the deliberately-ugly
# scaffold placeholder palette so the theme gate passes and the page looks designed.
_TOKENS_TEMPLATE = ''':root {
  /* Themed by the instant-landing pass from the brand accent; refined by the design pass. */
  --tk-primary: __ACCENT__;
  --tk-primary-foreground: #ffffff;
  --tk-accent: __ACCENT__;
  --tk-accent-foreground: #ffffff;
  --tk-background: #ffffff;
  --tk-foreground: #0f172a;
  --tk-muted: #f1f5f9;
  --tk-muted-foreground: #64748b;
  --tk-card: #ffffff;
  --tk-card-foreground: #0f172a;
  --tk-border: #e2e8f0;
  --tk-input: #e2e8f0;
  --tk-ring: __ACCENT__;
  --tk-destructive: #dc2626;
  --tk-destructive-foreground: #ffffff;
  --tk-radius: 0.625rem;
  --tk-font-sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --tk-font-heading: var(--tk-font-sans);
  --tk-font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
}
'''

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _tsx_string(value: str) -> str:
    """JSON-encode a value as a safe TSX string literal (handles quotes/newlines/unicode)."""
    return json.dumps(str(value or "").strip())


def _relative_luminance(r: int, g: int, b: int) -> float:
    """WCAG relative luminance of an sRGB color (0=black, 1=white)."""
    def _lin(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _normalize_accent(accent: str | None) -> str:
    """Validated brand accent, darkened if needed so the theme's hard-coded WHITE foreground stays
    readable on it. The accent fills primary buttons / the eyebrow / CTA links (all white text) and
    sits on a white page, so a light model-supplied accent (e.g. #ffd700) would be unreadable both
    ways. Darken until white text clears WCAG AA (contrast >= 4.5:1 vs #fff, i.e. luminance <= ~0.18)
    while keeping the hue, so this stays a one-knob theme with no per-color foreground branching."""
    raw = str(accent or "").strip()
    if not _HEX_RE.match(raw):
        return "#2563eb"  # clean default blue
    raw = raw.lower()
    r, g, b = int(raw[1:3], 16), int(raw[3:5], 16), int(raw[5:7], 16)
    for _ in range(12):
        if _relative_luminance(r, g, b) <= 0.18:
            break
        r, g, b = int(r * 0.85), int(g * 0.85), int(b * 0.85)
    return f"#{r:02x}{g:02x}{b:02x}"


def render_instant_landing_tsx(
    *,
    eyebrow: str,
    headline: str,
    subhead: str,
    primary_cta: str,
    features: list[dict[str, str]] | None,
) -> str:
    """Render a complete, compiling, branded landing.tsx from the brief fields."""
    feats: list[dict[str, str]] = []
    for f in (features or [])[:4]:
        if not isinstance(f, dict):
            continue
        title = str(f.get("title") or "").strip()
        body = str(f.get("body") or "").strip()
        if title and body:
            feats.append({"title": title, "body": body})
    if not feats:
        feats = [
            {"title": "Built for you", "body": "Set up in minutes and start right away."},
            {"title": "Simple and clear", "body": "No clutter — just what you need to get going."},
            {"title": "Always improving", "body": "New touches land as your product grows."},
        ]
    features_literal = (
        "[\n"
        + ",\n".join(
            f"  {{ title: {_tsx_string(f['title'])}, body: {_tsx_string(f['body'])} }}" for f in feats
        )
        + ",\n]"
    )
    return (
        _LANDING_TEMPLATE.replace("__EYEBROW__", _tsx_string(eyebrow or "Get started"))
        .replace("__HEADLINE__", _tsx_string(headline or "Welcome."))
        .replace("__SUBHEAD__", _tsx_string(subhead or "Sign in to get started."))
        .replace("__PRIMARY_CTA__", _tsx_string(primary_cta or "Continue with Google"))
        .replace("__FEATURES__", features_literal)
    )


def render_instant_tokens_css(*, accent: str | None) -> str:
    return _TOKENS_TEMPLATE.replace("__ACCENT__", _normalize_accent(accent))


def render_instant_index_html(existing_html: str, *, title: str, description: str) -> str:
    """Brand the scaffold ``index.html`` <head> at first paint: a real <title> + meta description
    from the idea brief, and strip the scaffold-placeholder instruction comment.

    The instant landing already rewrites ``landing.tsx`` + ``tokens.css`` (the visible page), but NOT
    ``index.html`` — so without this the browser TAB stays the bare site name + a generic description,
    and the ``SCAFFOLD-PLACEHOLDER`` comment ships to production, until the slow design pass rewrites
    the head minutes later. Best-effort string surgery: any field whose pattern is not found is left
    untouched, so the result is always valid HTML (a malformed index.html would fail the vite build
    and block the very first paint this exists to deliver)."""
    out = existing_html
    safe_title = html.escape((title or "").strip())
    safe_desc = html.escape((description or "").strip(), quote=True)
    if safe_title:
        out = re.sub(r"<title>.*?</title>", f"<title>{safe_title}</title>", out, count=1, flags=re.DOTALL)
    if safe_desc:
        out = re.sub(
            r'<meta\s+name="description"\s+content="[^"]*"\s*/?>',
            f'<meta name="description" content="{safe_desc}" />',
            out,
            count=1,
        )
    # Drop the scaffold instruction comment so it never reaches a customer's page source.
    out = re.sub(r"[ \t]*<!--\s*SCAFFOLD-PLACEHOLDER:.*?-->\n?", "", out, flags=re.DOTALL)
    return out
