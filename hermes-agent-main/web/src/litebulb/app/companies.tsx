/* Shared company-grid view. Each tile renders a REAL scaled embed of the
   company's live landing page (same-origin site-preview frame), with a graceful
   gradient-mock fallback until the site is published. The company list itself
   comes from the real operator home payload. */
import { useEffect, useRef, useState } from "react";
import "./companies.css";
import type { LitebulbBusiness } from "../takyon/useTakyonLitebulb";
import { buildTakyonBusinessSitePreviewFrameUrl } from "@/lib/api";

// Canonical product domain. Product sub-apps are served at
// `<slug>.coscale.app` (see product/Product.tsx canonicalProductHost and
// core._product_publish_target). The card address bar must show this real host —
// never a fabricated `.app` placeholder.
const PRODUCT_BASE_DOMAIN = "coscale.app";

function canonicalProductHost(slug: string) {
  const clean = (slug || "").toLowerCase().replace(/[^a-z0-9-]/g, "");
  return clean ? `${clean}.${PRODUCT_BASE_DOMAIN}` : "";
}

// Strip scheme/trailing slash so the chrome bar reads as a clean host.
function addressBarText(url: string) {
  return (url || "").replace(/^https?:\/\//i, "").replace(/\/$/, "");
}

/* Gradient "wireframe" placeholder — only used until the real landing page is
   published (or if the live embed fails to load). Keeps the tile readable
   instead of a blank/broken frame. */
function ThumbSkeleton({ name, tagline }: { name: string; tagline: string }) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  const bg = `radial-gradient(90% 80% at 50% -10%, hsl(${h} 80% 90%), hsl(${h} 45% 96%) 60%, #ffffff)`;
  const accent = `hsl(${h} 52% 48%)`;
  return (
    <div className="lb-thumb__page" style={{ background: bg }}>
      <div className="lb-thumb__nav">
        <span className="lb-thumb__brand"><span className="lb-thumb__logo" style={{ background: accent }} />{name}</span>
        <span className="lb-thumb__links"><i /><i /><i /></span>
        <span className="lb-thumb__navcta" style={{ background: accent }} />
      </div>
      <div className="lb-thumb__hero">
        <span className="lb-thumb__eyebrow" />
        <div className="lb-thumb__title">{tagline}</div>
        <div className="lb-thumb__subs"><i /><i /></div>
        <div className="lb-thumb__cta">Get started</div>
      </div>
    </div>
  );
}

/* A miniature of the company's own landing page — its tile, browser-framed.
   The view is a REAL scaled <iframe> of the live site served same-origin through
   the dashboard's site-preview endpoint (so cross-origin X-Frame-Options/CSP can
   never blank it). It is non-interactive (pointer-events:none, tabIndex -1) so
   the whole card stays a single click target. Falls back to the wireframe
   skeleton until the site is published or if the frame can't load. */
// Logical width the embedded landing page is rendered at before being scaled
// down to the tile. A desktop viewport so the full layout reads as a miniature.
const EMBED_LOGICAL_WIDTH = 1280;
const EMBED_LOGICAL_HEIGHT = 900;

export function LandingThumb({
  name,
  tagline,
  slug,
}: {
  name: string;
  tagline: string;
  slug: string;
}) {
  // 'embed' = live iframe rendering; 'skeleton' = published site missing / blocked.
  const [mode, setMode] = useState<"embed" | "skeleton">("embed");
  // Defer the (heavy) iframe mount until the tile scrolls into view. With N
  // companies this avoids N concurrent same-origin iframe document loads —
  // only visible tiles pay the network/memory cost. Off-screen tiles keep the
  // skeleton until they intersect the viewport.
  const [visible, setVisible] = useState(false);
  const viewRef = useRef<HTMLDivElement | null>(null);
  // Scale the 1280px-wide logical page down to the tile width. Measured from the
  // real container (a ResizeObserver keeps it correct across breakpoints) and
  // applied as an inline transform — `cqw` inside a transform calc() is dropped
  // by browsers, so we compute the ratio in JS instead of relying on it.
  const [scale, setScale] = useState(0);
  const url = addressBarText(canonicalProductHost(slug)) || name.toLowerCase().replace(/[^a-z0-9]/g, "");
  const frameUrl = slug ? buildTakyonBusinessSitePreviewFrameUrl(slug) : "";
  const showEmbed = visible && mode === "embed" && Boolean(frameUrl);

  // Mark the tile visible once it intersects the viewport, then stop observing.
  // If IntersectionObserver is unavailable, fall back to mounting immediately so
  // behavior is unchanged.
  useEffect(() => {
    const el = viewRef.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setVisible(true);
            io.disconnect();
            break;
          }
        }
      },
      { rootMargin: "200px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!showEmbed) return;
    const el = viewRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      if (w > 0) setScale(w / EMBED_LOGICAL_WIDTH);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [showEmbed]);

  return (
    <div className="lb-thumb">
      <div className="lb-thumb__bar"><i /><i /><i /><span className="lb-thumb__url">{url}</span></div>
      <div className="lb-thumb__view" ref={viewRef}>
        {showEmbed ? (
          <iframe
            className="lb-thumb__frame"
            src={frameUrl}
            title={`${name} landing preview`}
            loading="lazy"
            tabIndex={-1}
            aria-hidden="true"
            scrolling="no"
            sandbox="allow-scripts allow-same-origin"
            style={{
              width: `${EMBED_LOGICAL_WIDTH}px`,
              height: `${EMBED_LOGICAL_HEIGHT}px`,
              // Hide the unscaled frame for the first paint to avoid a flash of the
              // full-size page before the scale is measured.
              transform: scale ? `scale(${scale})` : "scale(0)",
              opacity: scale ? 1 : 0,
            }}
            // If the published site can't be embedded (not shipped yet, 404, or a
            // frame-policy block) drop to the readable wireframe skeleton.
            onError={() => setMode("skeleton")}
          />
        ) : (
          <ThumbSkeleton name={name} tagline={tagline} />
        )}
      </div>
    </div>
  );
}

/* Small brand mark left of the company name: the real published logo when one
   exists, otherwise a monogram chip so the row never breaks. */
function CompanyLogo({ slug, name, logoPath }: { slug: string; name: string; logoPath: string }) {
  const [broken, setBroken] = useState(false);
  // Use the PUBLIC product URL for the logo (same source the live landing favicon
  // uses — served 200 from R2 with no auth), not the token-gated workspace asset
  // endpoint, so the card reliably shows the REAL logo. logoPath is the workspace
  // path (e.g. "product/site/public/brand-logo.png"); its basename is the public
  // root path. onError still falls back to the monogram, so a missing/broken logo
  // never renders blank.
  const host = canonicalProductHost(slug);
  const src = logoPath && host ? `https://${host}/${logoPath.split("/").pop()}` : "";
  const monogram = (name.trim()[0] || "C").toUpperCase();
  if (!src || broken) {
    return <span className="lb-coCard__logo lb-coCard__logo--mono" aria-hidden="true">{monogram}</span>;
  }
  return (
    <img
      className="lb-coCard__logo"
      src={src}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setBroken(true)}
    />
  );
}

export function CompaniesGrid({
  companies,
  onOpen,
  onNew,
  onDelete,
}: {
  companies: LitebulbBusiness[];
  onOpen: (slug: string) => void;
  onNew: () => void;
  onDelete?: (slug: string) => void | Promise<void>;
}) {
  // Slug currently being deleted — disables its X and dims the card so a double-click can't double-fire.
  const [deleting, setDeleting] = useState<string>("");

  const handleDelete = async (slug: string, name: string) => {
    if (!onDelete || !slug || deleting) return;
    const confirmed = window.confirm(
      `Delete "${name}"?\n\nThis permanently removes the company, its product site, and all its data. This cannot be undone.`,
    );
    if (!confirmed) return;
    setDeleting(slug);
    try {
      await onDelete(slug);
    } finally {
      setDeleting("");
    }
  };

  return (
    <div className="lb-pf-grid">
      {companies.map((c, i) => {
        const isDeleting = deleting === c.slug;
        return (
          <div key={c.slug || c.name || i} className={`lb-coCard-wrap${isDeleting ? " is-deleting" : ""}`}>
            <button className="lb-coCard" onClick={() => onOpen(c.slug)} disabled={isDeleting}>
              <span className="lb-coCard__thumb"><LandingThumb name={c.name} tagline={c.tagline || c.goal || c.name} slug={c.slug} /></span>
              <span className="lb-coCard__brand">
                <CompanyLogo slug={c.slug} name={c.name} logoPath={c.logoPath} />
                <span className="lb-coCard__name">{c.name}</span>
              </span>
            </button>
            {onDelete && c.slug && (
              <button
                type="button"
                className="lb-coCard__delete"
                aria-label={`Delete ${c.name}`}
                title="Delete company"
                disabled={isDeleting}
                onClick={(e) => { e.stopPropagation(); void handleDelete(c.slug, c.name); }}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  <path d="M4 4l8 8M12 4l-8 8" />
                </svg>
              </button>
            )}
          </div>
        );
      })}
      <button className="lb-coCard lb-coCard--new" onClick={onNew}>
        <span className="lb-coCard__thumb lb-coCard__thumb--new"><span className="lb-coCard__plus">+</span></span>
        <span className="lb-coCard__brand"><span className="lb-coCard__name">New company</span></span>
        <span className="lb-coCard__meta">Describe an idea</span>
      </button>
    </div>
  );
}
