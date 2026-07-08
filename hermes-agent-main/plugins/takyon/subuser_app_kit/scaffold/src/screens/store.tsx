import { Link } from "react-router-dom";
import { Badge } from "../components/ui/badge";
import { Card, CardFooter, CardHeader, CardTitle } from "../components/ui/card";
import { businessDisplayName } from "../lib/branding";
import { hasStorefront, productCatalog, storePriceLabel } from "../lib/takyon";

// Same look as the landing hero's primary CTA link, so the Buy button matches the theme without
// re-deriving button internals. It is an <a>, not a <button>, because it navigates to Shopify's
// hosted checkout — the customer never touches our runtime (no new subuser-plane surface).
const buyButtonClass =
  "inline-flex h-11 w-full items-center justify-center rounded bg-primary px-4 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90";

/**
 * Buyable store section. Renders the business's published Shopify catalog — baked into the surface
 * context at publish time exactly like `plans` — with a Buy button per product that deep-links the
 * product's Shopify cart permalink (checkout happens on the merchant's own Shopify store). Renders
 * nothing when the business has no storefront, so non-Shopify sites are unaffected. Every business
 * inherits this the moment it pushes a product; there is no per-business bake.
 */
export function StoreSection({ heading = "Shop" }: { heading?: string }) {
  if (!hasStorefront()) return null;
  return (
    <section aria-labelledby="store-heading" className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h2 id="store-heading" className="font-heading text-2xl font-semibold text-foreground">
          {heading}
        </h2>
        <p className="text-sm text-muted-foreground">
          Secure checkout on Shopify — payment is completed on the store&apos;s hosted checkout.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {productCatalog.map((product) => {
          const priceLabel = storePriceLabel(product.price);
          return (
            <Card
              key={product.productId || product.handle || product.title}
              className="flex flex-col"
            >
              <CardHeader className="flex-1 gap-2">
                <CardTitle className="text-base">{product.title}</CardTitle>
                {priceLabel ? (
                  <Badge variant="outline" className="w-fit">
                    {priceLabel}
                  </Badge>
                ) : null}
              </CardHeader>
              <CardFooter>
                <a
                  href={product.cartPermalink}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={buyButtonClass}
                >
                  Buy now
                </a>
              </CardFooter>
            </Card>
          );
        })}
      </div>
    </section>
  );
}

/** Full-page store route (/store). */
export function StoreScreen() {
  const productName = businessDisplayName();
  return (
    <main className="min-h-screen bg-background">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-10 px-6 py-12 sm:py-16">
        <header className="flex items-center justify-between gap-4">
          <Link to="/" className="font-heading text-lg font-semibold text-foreground">
            {productName}
          </Link>
          <Link to="/" className="text-sm text-muted-foreground hover:text-foreground">
            ← Home
          </Link>
        </header>
        {hasStorefront() ? (
          <StoreSection heading="Shop" />
        ) : (
          <p className="text-sm text-muted-foreground">The store is coming soon.</p>
        )}
      </div>
    </main>
  );
}
