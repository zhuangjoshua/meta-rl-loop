import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Static SPA only: no server code, no API routes. `vite build` -> dist/.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // All app code consumes the Takyon kit through _takyon/ only. In real
      // products the platform overwrites _takyon/ wholesale, so this alias is
      // the single seam between app code and the platform-provided kit.
      "@takyon": new URL("./_takyon", import.meta.url).pathname,
    },
  },
  build: {
    outDir: "dist",
  },
});
