import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Relative asset URLs, so dist/ can be served from any mount point.
  base: "./",
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Fonts and images stay real files: nothing is inlined into HTML or CSS as data.
    assetsInlineLimit: 0,
    // No inline polyfill: index.html must carry external module scripts only, so a
    // Content-Security-Policy of script-src 'self' holds.
    modulePreload: { polyfill: false },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://localhost:8787", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
