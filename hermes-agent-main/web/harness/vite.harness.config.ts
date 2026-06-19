import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Standalone Vite dev server for the chat-rendering verification harness. It
// renders the REAL Product / Building components with mock workspace props so
// the headless-Chrome checks exercise the actual chat transcript code.
export default defineConfig({
  root: path.resolve(__dirname),
  plugins: [react()],
  publicDir: false,
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "../src"),
    },
  },
  server: {
    port: 5199,
    strictPort: true,
  },
});
