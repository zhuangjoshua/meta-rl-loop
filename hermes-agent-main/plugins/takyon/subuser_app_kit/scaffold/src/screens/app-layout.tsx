import { useEffect } from "react";
import { Link, NavLink, Outlet, useSearchParams } from "react-router-dom";
import { brandMarkDataUri, businessDisplayName } from "../lib/branding";
import { useProductAuth } from "../lib/product-auth";
import { useSubscribeIntent, useViewerAccess } from "../lib/hooks";

export function AppLayout() {
  const [searchParams, setSearchParams] = useSearchParams();
  const checkout = searchParams.get("checkout");
  const access = useViewerAccess();
  const auth = useProductAuth();
  const productName = businessDisplayName();
  // Turn the kit's `/app?intent=subscribe` CTA into a real checkout + Stripe redirect. Lives in the
  // shared layout so it works for every business regardless of how the generated Home screen renders
  // the subscribe button (the button is just a link to the intent route). Pass the reactive `intent`
  // so a client-side link click (not just a full reload) triggers checkout.
  useSubscribeIntent(access, searchParams.get("intent"));

  useEffect(() => {
    if (checkout !== "success" && checkout !== "cancel") return;
    const next = new URLSearchParams(searchParams);
    next.delete("checkout");
    setSearchParams(next, { replace: true });
  }, [checkout, searchParams, setSearchParams]);

  return (
    <div className="min-h-screen bg-background" data-takyon-scaffold="app-layout">
      <header className="border-b border-border bg-card/70 backdrop-blur">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-4">
          <div className="flex items-center gap-3">
            <img
              src={brandMarkDataUri()}
              alt={`${productName} logo`}
              className="h-9 w-9 rounded-lg"
              width={36}
              height={36}
            />
            <Link to="/" className="font-heading text-lg font-semibold text-foreground">
              {productName}
            </Link>
          </div>
          <nav className="flex flex-wrap items-center gap-2 text-sm">
            {[
              { to: "/", label: "Home" },
              { to: "/faq", label: "FAQ" },
              { to: "/app", label: "App" },
              { to: "/app/profile", label: "Account" },
            ].map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/" || item.to === "/app"}
                className={({ isActive }) =>
                  [
                    "rounded border px-3 py-2 transition-colors",
                    isActive
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-background text-foreground hover:bg-muted",
                  ].join(" ")
                }
              >
                {item.label}
              </NavLink>
            ))}
            {access.authenticated ? (
              <button
                type="button"
                onClick={() => void auth.logout()}
                disabled={auth.busy}
                className="rounded border border-border bg-background px-3 py-2 text-foreground transition-colors hover:bg-muted disabled:opacity-50"
              >
                Sign out
              </button>
            ) : null}
          </nav>
        </div>
      </header>
      {auth.error ? (
        <div className="border-b border-destructive/20 bg-destructive/10">
          <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-6 py-3 text-sm text-destructive">
            <span>{auth.error}</span>
            <button type="button" onClick={auth.clearError} className="font-medium underline underline-offset-4">
              Dismiss
            </button>
          </div>
        </div>
      ) : null}
      {auth.busy ? (
        <div className="border-b border-border bg-muted/60">
          <div className="mx-auto w-full max-w-6xl px-6 py-3 text-sm text-muted-foreground">
            Finishing secure sign-in…
          </div>
        </div>
      ) : null}
      <main className="mx-auto w-full max-w-6xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}
