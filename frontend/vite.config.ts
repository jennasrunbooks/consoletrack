/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { msw } from "msw/vite";
import { defineConfig, type Plugin } from "vite";

// In mock mode the MSW service worker answers /api in the browser. If a request still reaches
// the dev server (the browser restarted an idle worker, a hard reload skipped it), fail it
// loudly instead of proxying: mock and real data must never mix on one page.
const mockBypassGuard = (): Plugin => ({
  name: "ct-mock-bypass-guard",
  configureServer(server) {
    server.middlewares.use("/api", (_req, res) => {
      res.statusCode = 503;
      res.setHeader("Content-Type", "application/json");
      res.end(
        JSON.stringify({
          code: "mock_bypassed",
          message: "The request skipped the mock service worker; reload the page.",
        }),
      );
    });
  },
});

// `npm run dev` proxies /api to a local backend (Compose, `make local`, or a port-forward).
// `npm run dev:mock` (mode "mock") instead serves the MSW worker script, and src/main.tsx starts
// it so every request is answered in the browser from src/mocks/: no backend needed, and no
// proxy. Only mock mode loads these plugins, so production builds never contain the mocks.
export default defineConfig(({ mode }) => ({
  plugins: [react(), ...(mode === "mock" ? [msw({ mode: "worker-only" }), mockBypassGuard()] : [])],
  server: {
    proxy:
      mode === "mock"
        ? undefined
        : { "/api": { target: process.env.CT_BACKEND_URL ?? "http://localhost:8000", changeOrigin: true } },
  },
  test: {
    environment: "jsdom",
    // e2e/ is Playwright's (`npm run test:e2e`), not Vitest's.
    include: ["src/**/*.test.{ts,tsx}"],
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
  },
}));
