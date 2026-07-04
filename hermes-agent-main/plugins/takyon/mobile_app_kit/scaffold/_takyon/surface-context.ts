// PLATFORM-OWNED dev stub — do not edit. The platform materializes this per business at build
// (mobile surface materializer), baking the ABSOLUTE runtimeApiBase, auth config, selected rails,
// and branding. This stub lets `tsc`/dev boot before materialization; it must never ship live.
import type { SurfaceContext } from "./runtime-client";

export const surfaceContext: SurfaceContext = {
  runtimeApiBase: "https://__TAKYON_PRODUCT_HOST__/api/takyon/apps/__TAKYON_SLUG__",
  runtimeFeatures: ["auth", "account", "profile", "records", "generate"],
  railState: {},
  auth: {
    url: "__SUPABASE_URL__",
    publishableKey: "__SUPABASE_PUBLISHABLE_KEY__",
    googleProvider: "google",
  },
  branding: { accent: "#4f46e5", name: "__TAKYON_APP_NAME__" },
};
