import { Link } from "react-router-dom";
import {
  usePublicSiteNavigation,
  type PublicSiteHeaderAccess,
} from "../lib/public-site-navigation";

export function PublicSiteHeader({ access }: { access: PublicSiteHeaderAccess }) {
  const navigation = usePublicSiteNavigation(access);

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/95 shadow-sm backdrop-blur">
      <div className="flex w-full flex-wrap items-center justify-between gap-4 px-6 py-4 sm:px-8 lg:px-12">
        <Link to={navigation.homeHref} className="flex items-center gap-3">
          <img
            src={navigation.brandMarkSrc}
            alt={`${navigation.productName} logo`}
            className="h-10 w-10 rounded-lg"
            width={40}
            height={40}
          />
          <span className="font-heading text-lg font-semibold text-foreground">{navigation.productName}</span>
        </Link>

        {access.loading ? (
          <div className="h-10 w-72 animate-pulse rounded bg-muted" aria-label="Loading navigation" />
        ) : access.authenticated ? (
          <nav aria-label="Account navigation" className="flex flex-wrap items-center gap-2">
            {navigation.accountItems.map((item) => (
              <Link key={item.href} className="rounded px-3 py-2 text-sm font-medium text-foreground hover:bg-muted" to={item.href}>
                {item.label}
              </Link>
            ))}
            <button
              type="button"
              onClick={() => void navigation.signOut()}
              disabled={navigation.authBusy}
              className="rounded border border-border bg-card px-4 py-2 text-sm font-medium text-foreground hover:bg-muted disabled:opacity-50"
            >
              Sign out
            </button>
          </nav>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <nav aria-label="Public navigation" className="flex flex-wrap items-center gap-1">
              {navigation.publicItems.map((item) => (
                <Link key={item.href} className="rounded px-3 py-2 text-sm font-medium text-foreground hover:bg-muted" to={item.href}>
                  {item.label}
                </Link>
              ))}
            </nav>
            <button
              type="button"
              onClick={() => void navigation.logIn()}
              disabled={navigation.authDisabled}
              className="rounded border border-border bg-card px-4 py-2 text-sm font-medium text-foreground hover:bg-muted disabled:opacity-50"
            >
              Log in
            </button>
            <button
              type="button"
              onClick={() => void navigation.signUp()}
              disabled={navigation.authDisabled}
              className="rounded bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              Sign up
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
