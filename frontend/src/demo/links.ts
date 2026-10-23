export const REPO_URL = "https://github.com/jennasrunbooks/consoletrack";
export const HOW_IT_WORKS_URL = `${REPO_URL}#how-it-works`;

/** The demo's signed-in user. The role is admin so every button works against the mock state. */
export const DEMO_USER = { username: "demo", role: "admin" } as const;

/** Where the INC-14 and review steps of the tour land (src/mocks/fixtures.ts ids). */
export const TOUR_INCIDENT = 14;
export const TOUR_CHANGE = 42;
