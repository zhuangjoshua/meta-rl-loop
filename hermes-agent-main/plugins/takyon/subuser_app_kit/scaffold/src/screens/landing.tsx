import { Navigate } from "react-router-dom";
import { PublicSiteHeader } from "../components/site-navigation";
import { useViewerAccess } from "../lib/hooks";

/**
 * Runtime-only seed for the worker-owned public landing.
 *
 * AppKit owns the stable navigation and authenticated redirect, but deliberately
 * supplies no hero, content order, CTA placement, proof module, preview, or store
 * placement. The product worker must replace the marked canvas with the
 * business-specific landing before publication.
 */
export function LandingScreen() {
  const access = useViewerAccess();

  if (access.authenticated) return <Navigate to="/app" replace />;

  return (
    <main className="min-h-screen bg-background" aria-busy={access.loading || undefined}>
      <PublicSiteHeader access={access} />
      <div data-takyon-scaffold="landing" />
    </main>
  );
}
