import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import { ProductAuthProvider } from "./lib/product-auth";
import { LandingScreen } from "./screens/landing";

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
const TermsScreen = lazy(() =>
  import("./screens/support").then((m) => ({ default: m.TermsScreen })),
);
const ProfileScreen = lazy(() =>
  import("./screens/profile").then((m) => ({ default: m.ProfileScreen })),
);

const container = document.getElementById("root");
if (!container) throw new Error("root element missing");

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <ProductAuthProvider>
        <Suspense fallback={null}>
          <Routes>
            <Route path="/" element={<LandingScreen />} />
            <Route path="/faq" element={<FaqScreen />} />
            <Route path="/privacy" element={<PrivacyScreen />} />
            <Route path="/terms" element={<TermsScreen />} />
            <Route path="/articles" element={<ArticlesScreen />} />
            <Route path="/app" element={<AppLayout />}>
              <Route index element={<AppHomeScreen />} />
              <Route path="profile" element={<ProfileScreen />} />
            </Route>
          </Routes>
        </Suspense>
      </ProductAuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
