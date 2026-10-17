// Applies the remembered or OS theme before first paint, so dark mode doesn't flash white.
// A plain file rather than an inline script, so the CSP can stay script-src 'self'.
try {
  var t = localStorage.getItem("ct-theme");
  if (t !== "light" && t !== "dark") t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = t;
} catch (e) {
  // Storage blocked: src/lib/theme.tsx falls back to the OS setting after load.
}
