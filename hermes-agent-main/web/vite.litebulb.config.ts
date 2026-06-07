import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  publicDir: false,
  base: "./",
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: "../takyon_cli/web_dist/litebulb",
    emptyOutDir: false,
    rollupOptions: {
      input: path.resolve(__dirname, "litebulb.html"),
    },
  },
});
