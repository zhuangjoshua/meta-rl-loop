import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import { ActionErrorAnnouncer } from "./components/action-error-announcer";
import { SocialProofMarquee } from "./components/social-proof-marquee";
import { SubscriptionCancellation } from "./components/subscription-cancellation";
import { installInteractionSounds } from "./lib/interaction-sounds";
import { useViewerAccess } from "./lib/hooks";
import { ProductAuthProvider } from "./lib/product-auth";
import { LandingScreen } from "./screens/landing";
// Not lazy: the store lives on the landing chunk already (StoreSection is rendered inline on the
// landing page), so a separate lazy chunk for /store would never be split out anyway.
import { StoreScreen } from "./screens/store";

const AppHomeScreen = lazy(() =>
  import("./screens/app-home").then((m) => ({ default: m.AppHomeScreen })),
);
const AppLayout = lazy(() =>
  import("./screens/app-layout").then((m) => ({ default: m.AppLayout })),
);
const ArticlesScreen = lazy(() =>
  import("./screens/support").then((m) => ({ default: m.ArticlesScreen })),
);
const FaqScreen = lazy(() =>
  import("./screens/support").then((m) => ({ default: m.FaqScreen })),
);
const PrivacyScreen = lazy(() =>
  import("./screens/support").then((m) => ({ default: m.PrivacyScreen })),
);
const PricingScreen = lazy(() =>
  import("./screens/support").then((m) => ({ default: m.PricingScreen })),
);
const TermsScreen = lazy(() =>
  import("./screens/support").then((m) => ({ default: m.TermsScreen })),
);
const ProfileScreen = lazy(() =>
  import("./screens/profile").then((m) => ({ default: m.ProfileScreen })),
);

function AccountRoute() {
  return (
    <div className="grid gap-6">
      <ProfileScreen />
      <SubscriptionCancellation />
    </div>
  );
}

function PublicLandingRoute() {
  const access = useViewerAccess();
  if (access.loading || access.authenticated) return <LandingScreen />;
  return (
    <>
      <LandingScreen />
      <SocialProofMarquee />
    </>
  );
}

const container = document.getElementById("root");
if (!container) throw new Error("root element missing");

installInteractionSounds();

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <ProductAuthProvider>
        <ActionErrorAnnouncer />
        <Suspense fallback={null}>
          <Routes>
            <Route path="/" element={<PublicLandingRoute />} />
            <Route path="/store" element={<StoreScreen />} />
            <Route path="/faq" element={<FaqScreen />} />
            <Route path="/pricing" element={<PricingScreen />} />
            <Route path="/privacy" element={<PrivacyScreen />} />
            <Route path="/terms" element={<TermsScreen />} />
            <Route path="/articles" element={<ArticlesScreen />} />
            <Route path="/app" element={<AppLayout />}>
              <Route index element={<AppHomeScreen />} />
              <Route path="profile" element={<AccountRoute />} />
            </Route>
          </Routes>
        </Suspense>
      </ProductAuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
