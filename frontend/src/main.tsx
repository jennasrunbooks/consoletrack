import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-sans/latin-700.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "./styles.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { DemoErrorPage } from "./demo/DemoErrorPage";
import { ensureStorage } from "./demo/memoryStorage";
import { DEMO } from "./lib/mode";
import { ThemeProvider } from "./lib/theme";

// Vite's BASE_URL ends in "/"; the router wants the prefix without it ("" at the domain root).
const BASENAME = import.meta.env.BASE_URL.replace(/\/$/, "");

async function start() {
  // `npm run dev:mock` and the demo build: answer every API call in the browser. The import is
  // inside the check, and the check is spelled out here, so production builds leave the mocks out
  // entirely (Rollup doesn't fold a constant imported from another module before it prunes the import).
  let mockFailed = false;
  if (import.meta.env.MODE === "mock" || import.meta.env.MODE === "demo") {
    try {
      if (DEMO) ensureStorage();
      const { startMockWorker } = await import("./mocks/browser");
      await startMockWorker();
    } catch (err) {
      if (!DEMO) throw err;
      mockFailed = true; // no service worker support or permission
    }
  }
  // The demo has no backend to fall back on: without the worker in control of this page (a hard
  // reload skips it), the first /api call would leave the browser, so ask for a reload instead.
  const unanswered = DEMO && (mockFailed || !navigator.serviceWorker?.controller);
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <ThemeProvider>
        <BrowserRouter basename={BASENAME}>
          {unanswered ? (
            <DemoErrorPage />
          ) : (
            <AuthProvider>
              <App />
            </AuthProvider>
          )}
        </BrowserRouter>
      </ThemeProvider>
    </StrictMode>,
  );
}

void start();
