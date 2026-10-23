/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import type { ServerResponse } from "node:http";
import { msw } from "msw/vite";
import { defineConfig, loadEnv, type Plugin } from "vite";

// In mock mode the MSW service worker answers /api in the browser. If a request still reaches
// the dev server (the browser restarted an idle worker, a hard reload skipped it), fail it
// loudly instead of proxying: mock and real data must never mix on one page.
const mockBypassGuard = (): Plugin => {
  const guard = (_req: unknown, res: ServerResponse) => {
    res.statusCode = 503;
    res.setHeader("Content-Type", "application/json");
    res.end(
      JSON.stringify({
        code: "mock_bypassed",
        message: "The request skipped the mock service worker; reload the page.",
      }),
    );
  };
  return {
    name: "ct-mock-bypass-guard",
    configureServer(server) {
      server.middlewares.use("/api", guard);
    },
    // `vite preview --mode demo` serves the built demo the same way; without this its SPA
    // fallback would answer a leaked /api request with index.html.
    configurePreviewServer(server) {
      server.middlewares.use("/api", guard);
    },
  };
};

// `vite build --mode demo` (npm run build:demo) is the recorded demo site: the mock build plus
// the files a static host needs. GitHub Pages answers an unknown path with 404.html, so a copy of
// index.html there makes deep links (/incidents/14) load the app instead of a 404 page.
const spaFallback = (): Plugin => ({
  name: "ct-demo-spa-fallback",
  apply: "build",
  enforce: "post",
  generateBundle(_options, bundle) {
    const index = bundle["index.html"];
    if (index?.type !== "asset") return;
    this.emitFile({ type: "asset", fileName: "404.html", source: index.source });
  },
});

// `npm run dev` proxies /api to a local backend (Compose, `make local`, or a port-forward).
// `npm run dev:mock` (mode "mock") instead serves the MSW worker script, and src/main.tsx starts
// it so every request is answered in the browser from src/mocks/: no backend needed, and no
// proxy. Only mock mode loads these plugins, so production builds never contain the mocks.
//
// Mode "demo" is the same mock setup, built for a static host: the worker script is emitted into
// the build, `base` comes from VITE_BASE (a custom domain serves from /), and 404.html is added.
export default defineConfig(({ mode }) => {
  const mocked = mode === "mock" || mode === "demo";
  const base = loadEnv(mode, process.cwd(), "VITE_").VITE_BASE || "/";
  return {
    base,
    plugins: [
      react(),
      ...(mocked ? [msw({ mode: "worker-only" }), mockBypassGuard()] : []),
      ...(mode === "demo" ? [spaFallback()] : []),
    ],
    server: {
      proxy: mocked
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
  };
});
