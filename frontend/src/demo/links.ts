export const REPO_URL = "https://github.com/jennasrunbooks/consoletrack";
export const HOW_IT_WORKS_URL = `${REPO_URL}#how-it-works`;

/** The demo's signed-in user. The role is admin so every button works against the mock state. */
export const DEMO_USER = { username: "demo", role: "admin" } as const;

/** Where the tour's steps land (src/mocks/fixtures.ts ids): INC-11 was fixed by the policy gate on its own,
 * INC-14 is the recorded OOM run that waits for an admin's Approve. */
export const TOUR_AUTO_INCIDENT = 11;
export const TOUR_INCIDENT = 14;
export const TOUR_CHANGE = 42;
