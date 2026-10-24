// Build modes (vite --mode): "mock" is `npm run dev:mock`, "demo" is the recorded demo site
// (`npm run build:demo`). Both answer /api from src/mocks/ in the browser; production builds
// have neither, and Vite folds these to constants so the guarded code drops out of them.
export const DEMO = import.meta.env.MODE === "demo";
export const SAMPLE_DATA = DEMO || import.meta.env.MODE === "mock";
