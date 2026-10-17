/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { msw } from "msw/vite";
import { defineConfig } from "vite";

// `npm run dev` proxies /api to a local backend (Compose, `make local`, or a port-forward).
// `npm run dev:mock` (mode "mock") also serves the MSW worker script, and src/main.tsx starts it
// so every request is answered in the browser from src/mocks/: no backend needed. Only mock mode
// loads the plugin, so production builds never contain the worker or the mocks.
export default defineConfig(({ mode }) => ({
  plugins: [react(), mode === "mock" && msw({ mode: "worker-only" })],
  server: {
    proxy: {
      "/api": { target: process.env.CT_BACKEND_URL ?? "http://localhost:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
  },
}));
