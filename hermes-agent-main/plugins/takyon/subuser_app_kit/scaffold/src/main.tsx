import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import { ProductAuthProvider } from "./lib/product-auth";
import { AppHomeScreen } from "./screens/app-home";
import { AppLayout } from "./screens/app-layout";
import { LandingScreen } from "./screens/landing";
import { ArticlesScreen, FaqScreen, PrivacyScreen, TermsScreen } from "./screens/support";
import { ProfileScreen } from "./screens/profile";

const container = document.getElementById("root");
if (!container) throw new Error("root element missing");

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <ProductAuthProvider>
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
      </ProductAuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
