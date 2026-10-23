# frontend

React + Vite + TypeScript dashboard for ConsoleTrack. Screens follow
[`../docs/mockups/`](../docs/mockups/) in both themes; the contract is
[`../openapi.yaml`](../openapi.yaml).

Built for #13: app shell, login, intake indicator, change log (PR 1); incident list and incident
detail with evidence, Approve/Reject, and Re-run triage (PR 2a); the remediation outcome (PR 2b);
change detail with Revert, Accept, and Codify (Phase 2), its evidence, and the postmortem editor
(PR 2c).

## Commands

| Task | Command |
|---|---|
| Install (lockfile, no install scripts) | `npm ci --ignore-scripts` |
| Dev against mocks, no backend | `npm run dev:mock` → http://localhost:5173 (log in as `admin`/`admin` or `viewer`/`viewer`) |
| Dev against a backend on :8000 | `npm run dev` (`CT_BACKEND_URL` to point elsewhere); open it on `localhost`, not the printed `Network:` address, or the browser drops the `Secure` session cookie |
| Regenerate API types after editing `openapi.yaml` | `npm run gen:api` |
| Lint, typecheck, API types up to date, tests | `make test-frontend` from the repo root (Vitest, then `make test-e2e`; `SKIP_E2E=1` leaves the browser out) |
| Browser tests (Playwright, Chromium) | `make test-e2e`: starts `npm run dev:mock` on 127.0.0.1:5173; uses `CHROMIUM_PATH` if set, else `npx playwright install chromium` once |
| Accessibility (axe-core, WCAG 2.1 AA, both themes) | part of `make test-e2e` (`e2e/a11y.spec.ts`) |
| Regenerate `docs/screenshots/` | `make screenshots` (1440×900 at 2×, both themes) |
| Production build | `npm run build` → `dist/` |
| Recorded demo site | `npm run build:demo` (or `make demo-site`) → `dist-demo/`; try it with `npm run preview:demo` |
| Demo banner and tour screenshots | `npm run screenshots:demo` (rewrites `docs/screenshots/demo-*.png`) |
| Image (nginx, non-root, :8080, `/healthz`) | `docker buildx build --platform linux/arm64 frontend/` |

## Layout

```text
src/api/          the only code that calls the backend
  schema.d.ts     generated from ../openapi.yaml by `npm run gen:api` (don't edit; CI checks it)
  client.ts       typed calls (openapi-fetch), one ApiError for every non-2xx
src/auth/         session state from GET /auth/me; login and logout
src/components/   shell: header, intake indicator, user menu, theme toggle, AdminButton,
                  Pagination, EvidenceList, AnalysisDetails, Markdown (sanitized)
src/pages/        login, change log and incident list (filters in the URL), change detail
                  (Revert/Accept/Codify; polls while detected or reverting), incident detail
                  (polls every 5 s while open or triaging) with the postmortem editor
src/lib/          formatting, theme, small data hooks
src/mocks/        MSW handlers + fixtures shared by tests and `npm run dev:mock`
src/test/         Vitest setup and render helpers
e2e/              Playwright specs; fixtures.ts fails any test that requests beyond localhost
```

## Rules this code keeps

- **Only `src/api/` calls the backend.** ESLint blocks `fetch` and `openapi-fetch` elsewhere,
  and `grep -R "fetch(" src | grep -v src/api` finds nothing.
- **API text is text.** No `dangerouslySetInnerHTML` or `innerHTML` (ESLint blocks both);
  CloudTrail-sourced fields are attacker-influenced (openapi.yaml → Untrusted text). Links built
  from API values go through `safeHttpUrl`.
- **Markdown goes through `<Markdown>`.** react-markdown builds React elements (no HTML string),
  drops raw HTML, and rehype-sanitize applies an allowlist without images; links open with
  `rel="noopener noreferrer"` and `javascript:` URLs are removed.
- **The session is the `HttpOnly` cookie.** Nothing stores a token. `localStorage` holds only
  the theme choice, wrapped in try/catch.
- **Writes are admin-only.** Use `AdminButton`: viewers see it disabled with an "Admin only"
  tooltip; the backend enforces the same with 403.
- **Tests never touch the network.** MSW answers every request and fails the test on any
  request without a handler. Playwright tests abort and fail on any request beyond localhost,
  and Chromium resolves no other hostname. They seed mock state through `window.__ctMock`
  (`src/mocks/browser.ts`), since MSW's service worker answers before `page.route` sees it. Fixtures are typed against `schema.d.ts`, so a contract change
  that breaks them fails `typecheck`.
- **Colors are CSS variables** in `src/styles.css`, one set per theme. The theme follows the OS
  until the header toggle sets it; `public/theme-init.js` applies it before first paint.

## Recorded demo site

`npm run build:demo` publishes the mock mode as a static site (GitHub Pages; the workflow is proposed in
[`../docs/proposed-workflows/pages.yaml`](../docs/proposed-workflows/pages.yaml)). It differs from `dev:mock`:

- **Signed in as `demo` (admin) on every load**, with no login form; logging out lands on a "Restart demo" page.
  Nothing is stored, so a reload resets the state.
- **Banner on every page** ("Recorded demo · sample data, no live AWS", with video, How it works, and Source links) and a
  dismissible first-visit tour (dismissal kept in `localStorage`, with an in-memory fallback when storage is blocked).
- **Intake shows Replay** with "Recorded: events replayed from fixtures", and no Grafana link.
- **`/api` can't leave the browser.** The worker answers unknown `/api` calls with 503 `mock_bypassed`, `vite preview`
  does the same, and a page the worker doesn't control shows "Reload the demo".
- **Static hosting:** `VITE_BASE` (default `/`) sets Vite's `base` and the router's `basename`; the worker script and a
  `404.html` copy of `index.html` (for deep links on Pages) are emitted into the build. `VITE_DEMO_VIDEO_URL` sets the
  video link (hidden when empty).

Checks: `src/test/bundle.test.ts` builds both modes and fails if the production bundle contains the worker, fixtures, or demo
strings, or if the demo bundle contains a 12-digit number other than `123456789012` or an IPv4 address outside the
documentation, private, and `0.0.0.0` ranges. `e2e/demo.spec.ts` runs against the built demo (part of `make test-e2e`).
To refresh the recorded incident, see below; the demo picks it up on the next build.

## Mock data

`src/mocks/fixtures.ts` follows a real kind run: the demo-app chart's alert and faults, and only
k8s-mcp's tools with their real result shapes. INC-14 goes further: `src/mocks/recorded-oom.ts` is
generated from a real capture of the OOM fault, field for field, with ids changed to INC-14's and
every timestamp re-based (`src/mocks/rebase.ts`) so it reads as 9 minutes old.

To re-capture after the agent, k8s-mcp, or the fault changes: on the host, `make local`, then
`make local-fault FAULT=oom`, wait for the incident (`make local-incidents`), and from the repo root:

```bash
base=http://localhost:8080
pw=$(docker compose -f docker-compose.yaml -f compose/kind.yaml --profile kind exec -T backend cat /run/consoletrack/admin-password)
jar=$(mktemp)
jq -n --arg p "$pw" '{username: "admin", password: $p}' |
  curl -fsS -c "$jar" -o /dev/null -H 'Content-Type: application/json' --data @- "$base/api/v1/auth/login"
id=$(curl -fsS -b "$jar" "$base/api/v1/incidents?page_size=1" | jq '.items[0].id')
curl -fsS -b "$jar" "$base/api/v1/incidents/$id" | jq . > incident.json
curl -fsS -b "$jar" "$base/api/v1/incidents/$id/evidence" | jq . > evidence.json
rm -f "$jar"
```

Read both files before using them (no secrets or account IDs), then
`cd frontend && npm run record:incident -- ../incident.json ../evidence.json` and delete them.
