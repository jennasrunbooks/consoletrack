import { changeRows, expect, logIn, test } from "./fixtures";

test("admin logs in and sees the change log", async ({ page }) => {
  await logIn(page, "admin");
  await expect(changeRows(page)).toHaveCount(6);

  await page.getByRole("button", { name: "Account: admin" }).click();
  const menu = page.getByRole("region", { name: "Account" });
  await expect(menu).toContainText("Signed in as admin");
  await expect(menu).toContainText("Admin: can revert, accept, and approve.");
});

test("viewer logs in read-only", async ({ page }) => {
  await logIn(page, "viewer");
  await expect(changeRows(page)).toHaveCount(6);

  await page.getByRole("button", { name: "Account: viewer" }).click();
  const menu = page.getByRole("region", { name: "Account" });
  await expect(menu).toContainText("Signed in as viewer");
  await expect(menu).toContainText("Viewer: read-only.");
});

test("a wrong password is rejected", async ({ page }) => {
  await page.goto("/changes");
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Wrong username or password.");
  await expect(page.getByLabel("Password")).toHaveValue("");
});

test("login keeps the filtered URL", async ({ page }) => {
  await logIn(page, "viewer", "/changes?risk=high");
  await expect(page).toHaveURL(/[?&]risk=high/);
  await expect(changeRows(page)).toHaveCount(3);
});

test("logout returns to the login screen and stays there after a reload", async ({ page }) => {
  await logIn(page, "admin");
  await page.getByRole("button", { name: "Account: admin" }).click();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Change log" })).toHaveCount(0);
});
