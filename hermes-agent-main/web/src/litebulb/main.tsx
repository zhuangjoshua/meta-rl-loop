import React from "react";
import { createRoot } from "react-dom/client";

// Token + base layers loaded once, globally:
//  • base44.css  → --b44-* tokens + the marketing component classes
//  • tokens.scss → --cds-* tokens (composer) on :root, plus [data-theme="dark"] remap
//  • litebulb.css → the unified --lb-* identity; re-points BOTH namespaces above to
//    Litebulb's brand. Imported LAST so its :root overrides win the cascade.
import "./styles/globals.css";
import "./base44/base44.css";
import "./composer-ui/styles/tokens.scss";
import "./styles/litebulb.css";

import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
