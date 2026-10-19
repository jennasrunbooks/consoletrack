# frontend

React + Vite + TypeScript dashboard for ConsoleTrack. Screens follow
[`../docs/mockups/`](../docs/mockups/) in both themes; the contract is
[`../openapi.yaml`](../openapi.yaml).

Built so far (#13, PR 1): app shell, login, intake indicator, change log. Change detail,
incidents, evidence, and the postmortem editor come in PR 2; their routes are placeholders.

## Commands

| Task | Command |
|---|---|
| Install (lockfile, no install scripts) | `npm ci --ignore-scripts` |
| Dev against mocks, no backend | `npm run dev:mock` → http://localhost:5173 (log in as `admin`/`admin` or `viewer`/`viewer`) |
| Dev against a backend on :8000 | `npm run dev` (`CT_BACKEND_URL` to point elsewhere); open it on `localhost`, not the printed `Network:` address, or the browser drops the `Secure` session cookie |
| Regenerate API types after editing `openapi.yaml` | `npm run gen:api` |
| Lint, typecheck, API types up to date, tests | `make test-frontend` from the repo root |
| Production build | `npm run build` → `dist/` |
| Image (nginx, non-root, :8080, `/healthz`) | `docker buildx build --platform linux/arm64 frontend/` |

## Layout

```text
src/api/          the only code that calls the backend
  schema.d.ts     generated from ../openapi.yaml by `npm run gen:api` (don't edit; CI checks it)
  client.ts       typed calls (openapi-fetch), one ApiError for every non-2xx
src/auth/         session state from GET /auth/me; login and logout
src/components/   shell: header, intake indicator, user menu, theme toggle, AdminButton
src/pages/        login, change log (filters in the URL), PR 2 placeholders
src/lib/          formatting, theme, small data hooks
src/mocks/        MSW handlers + fixtures shared by tests and `npm run dev:mock`
src/test/         Vitest setup and render helpers
```

## Rules this code keeps

- **Only `src/api/` calls the backend.** ESLint blocks `fetch` and `openapi-fetch` elsewhere,
  and `grep -R "fetch(" src | grep -v src/api` finds nothing.
- **API text is text.** No `dangerouslySetInnerHTML` or `innerHTML` (ESLint blocks both);
  CloudTrail-sourced fields are attacker-influenced (openapi.yaml → Untrusted text). Links built
  from API values go through `safeHttpUrl`.
- **The session is the `HttpOnly` cookie.** Nothing stores a token. `localStorage` holds only
  the theme choice, wrapped in try/catch.
- **Writes are admin-only.** Use `AdminButton`: viewers see it disabled with an "Admin only"
  tooltip; the backend enforces the same with 403.
- **Tests never touch the network.** MSW answers every request and fails the test on any
  request without a handler. Fixtures are typed against `schema.d.ts`, so a contract change
  that breaks them fails `typecheck`.
- **Colors are CSS variables** in `src/styles.css`, one set per theme. The theme follows the OS
  until the header toggle sets it; `public/theme-init.js` applies it before first paint.
