// Builds the app twice, the way `npm run build` and `npm run build:demo` do, and checks what ends
// up in each bundle: the production one must carry no mock code, and the demo one (public, on
// GitHub Pages) must carry no real account IDs or public IP addresses.
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { extname, join } from "node:path";
import { build } from "vite";

const TEXT = new Set([".js", ".css", ".html", ".json", ".svg", ".map", ".txt"]);

function bundleFiles(dir: string): Map<string, string> {
  const files = new Map<string, string>();
  const walk = (current: string) => {
    for (const name of readdirSync(current)) {
      const path = join(current, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (TEXT.has(extname(name))) files.set(path.slice(dir.length + 1), readFileSync(path, "utf8"));
    }
  };
  walk(dir);
  return files;
}

async function buildBundle(mode: string, dir: string): Promise<Map<string, string>> {
  await build({ mode, logLevel: "silent", build: { outDir: dir, emptyOutDir: true, reportCompressedSize: false } });
  return bundleFiles(dir);
}

const dirs: string[] = [];
const outDir = (name: string) => {
  const dir = mkdtempSync(join(tmpdir(), `ct-${name}-`));
  dirs.push(dir);
  return dir;
};
afterAll(() => {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true });
});

/** Files whose text matches, for a readable failure. */
const filesMatching = (files: Map<string, string>, pattern: RegExp) =>
  [...files].filter(([, text]) => pattern.test(text)).map(([name]) => name);

describe("production bundle", () => {
  let files: Map<string, string>;
  beforeAll(async () => {
    files = await buildBundle("production", outDir("prod"));
  }, 120_000);

  it("has no mock service worker, fixtures, or demo chrome", () => {
    expect([...files.keys()]).not.toContain("mockServiceWorker.js");
    expect([...files.keys()]).not.toContain("404.html");
    // One marker per kind: the worker, fixture data (the placeholder account, a fixture title,
    // the recorded incident), the mock login, and the demo build's own strings.
    const markers = /mockServiceWorker|setupWorker|123456789012|SSH opened to the internet|recorded-oom|mock_bypassed|Recorded demo|Restart demo/;
    expect(filesMatching(files, markers)).toEqual([]);
  });
});

describe("demo bundle", () => {
  let files: Map<string, string>;
  beforeAll(async () => {
    files = await buildBundle("demo", outDir("demo"));
  }, 120_000);

  it("ships the worker script and the 404.html Pages serves for deep links", () => {
    expect([...files.keys()]).toEqual(expect.arrayContaining(["mockServiceWorker.js", "404.html", "index.html"]));
    expect(files.get("404.html")).toBe(files.get("index.html"));
  });

  it("has no account ID but the placeholder", () => {
    const found = new Set<string>();
    for (const [name, text] of files) {
      if (name === "mockServiceWorker.js") continue;
      for (const [id] of text.matchAll(/(?<![\d.])\d{12}(?!\d)/g)) if (id !== "123456789012") found.add(`${name}: ${id}`);
    }
    expect([...found]).toEqual([]);
  });

  it("has no public IPv4 address", () => {
    const found = new Set<string>();
    for (const [name, text] of files) {
      for (const match of text.matchAll(/(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])/g)) {
        const octets = match.slice(1).map(Number) as [number, number, number, number];
        if (octets.every((o) => o <= 255) && !isAllowedAddress(octets)) found.add(`${name}: ${match[0]}`);
      }
    }
    expect([...found]).toEqual([]);
  });
});

/** Documentation ranges (RFC 5737), private ranges (RFC 1918), and 0.0.0.0 (as in 0.0.0.0/0). */
function isAllowedAddress([a, b, c, d]: [number, number, number, number]): boolean {
  if (a === 0 && b === 0 && c === 0 && d === 0) return true;
  if (a === 10) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a === 192 && b === 0 && c === 2) return true;
  if (a === 198 && b === 51 && c === 100) return true;
  return a === 203 && b === 0 && c === 113;
}
