import { expect, test } from "@playwright/test";
import { latestEmailTo, PASSWORD, totp, uniqueEmail } from "./helpers";

test("sign up, verify, set up MFA, add a business and submit it for review", async ({ page }) => {
  const email = uniqueEmail("owner");

  await page.goto("/signup");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();

  const link = latestEmailTo(email).body.match(/https?:\/\/\S+/)![0];
  await page.goto(new URL(link).pathname + new URL(link).search);
  await expect(page.getByText("Your email is verified.")).toBeVisible();
  await page.getByRole("link", { name: "Log in" }).click();

  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(page.getByRole("heading", { name: "Set up two-step verification" })).toBeVisible();
  await expect(page.getByRole("img", { name: "QR code for your authenticator app" })).toBeVisible();
  const secret = (await page.getByTestId("mfa-secret").textContent())!.trim();
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Turn on and continue" }).click();

  await expect(page.getByRole("heading", { name: "Your businesses" })).toBeVisible();
  await expect(page.getByRole("note")).toContainText("No real money moves");

  const slug = `e2e-${Date.now()}`;
  await page.getByLabel("Trading name").fill("E2E Café");
  await page.getByLabel("Legal name").fill("E2E Café Limited");
  await page.getByLabel("Payment link name").fill(slug);
  await page.getByRole("button", { name: "Add business" }).click();

  await expect(page.getByRole("heading", { name: "E2E Café" })).toBeVisible();
  await expect(page.getByText("Draft", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Submit for review" }).click();
  await expect(page.getByText("Pending review", { exact: true })).toBeVisible();

  // Log out, then log back in with the authenticator code.
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("6-digit code").fill(totp(secret, 1));
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("link", { name: /E2E Café/ })).toBeVisible();
});

test("protected pages send logged-out visitors to log in", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/login$/);
});

test("wrong password shows a clear message", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(uniqueEmail("nobody"));
  await page.getByLabel("Password").fill("Wrong-password-123");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("That email and password don't match.")).toBeVisible();
});
