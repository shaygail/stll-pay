import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { apiEnv } from "../playwright.config";
import { latestEmailTo, PASSWORD, totp, uniqueEmail } from "./helpers";

/**
 * Launch mode: STLL HAUS only. Public sign-up is closed; the administrator is created by
 * the bootstrap script and staff join by invitation.
 */

function bootstrap(adminEmail: string): { invite_link: string } {
  const out = execFileSync(
    "uv",
    [
      "run", "python", "scripts/bootstrap_stll_haus.py",
      "--admin-email", adminEmail,
      "--account-number", "01-0123-0123456-00",
      "--account-name", "STLL HAUS LIMITED",
      "--verified-by-evidence", "e2e test fixture",
    ],
    { cwd: path.resolve(__dirname, "../../api"), env: { ...process.env, ...apiEnv }, encoding: "utf8" },
  );
  return JSON.parse(out.slice(out.indexOf("{")));
}

async function acceptInviteAndLogIn(page: Page, link: string, email: string): Promise<string> {
  await page.goto(new URL(link).pathname + new URL(link).search);
  await page.getByLabel("New password").fill(PASSWORD);
  await page.getByLabel("Type it again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(page.getByText("Your password is set.")).toBeVisible();
  await page.getByRole("link", { name: "Log in" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Set up two-step verification" })).toBeVisible();
  const secret = (await page.getByTestId("mfa-secret").textContent())!.trim();
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Turn on and continue" }).click();
  return secret;
}

test("public sign-up is closed at launch", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("open to STLL HAUS only")).toBeVisible();
  await expect(page.getByRole("link", { name: "Create a business account" })).toHaveCount(0);
  await page.goto("/signup");
  await expect(page.getByRole("heading", { name: "Sign-up isn't open yet" })).toBeVisible();
});

test("STLL HAUS admin sets up, takes payments, and invites a cashier", async ({ page, context }) => {
  const adminEmail = uniqueEmail("admin");
  const { invite_link } = bootstrap(adminEmail);
  await acceptInviteAndLogIn(page, invite_link, adminEmail);

  // One business: straight into STLL HAUS, no list of businesses to choose from.
  await expect(page.getByRole("heading", { name: "STLL HAUS", level: 1 })).toBeVisible();
  await expect(page).toHaveURL(/\/dashboard\/[0-9a-f-]+$/);
  await expect(page.getByRole("link", { name: "All businesses" })).toHaveCount(0);
  await expect(page.getByRole("note")).toContainText("No real money moves");
  await expect(page.getByText("STLL HAUS LIMITED ··5600")).toBeVisible();

  // Counter QR code.
  await page.getByLabel("Label").fill("Front counter");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("img", { name: "QR code: Front counter" })).toBeVisible();

  // Payment link.
  await page.getByLabel("What it's for").fill("Catering deposit");
  await page.getByLabel("Amount (NZD, optional)").fill("45");
  await page.getByRole("button", { name: "Create link" }).click();
  await expect(page.getByText("Catering deposit · $45.00")).toBeVisible();

  // Invite a cashier.
  const cashierEmail = uniqueEmail("cashier");
  await page.getByLabel("Team member's email", { exact: true }).fill(cashierEmail);
  await page.getByLabel("Role").selectOption("cashier");
  await page.getByRole("button", { name: "Invite" }).click();
  await expect(page.getByText("Invitation sent.")).toBeVisible();
  await expect(page.getByText("cashier · invite sent")).toBeVisible();

  // The cashier accepts in another browser and lands in STLL HAUS with cashier rights.
  const cashierContext = await context.browser()!.newContext();
  const cashier = await cashierContext.newPage();
  const link = latestEmailTo(cashierEmail).body.match(/https?:\/\/\S+/)![0];
  await acceptInviteAndLogIn(cashier, link, cashierEmail);
  await expect(cashier.getByRole("heading", { name: "STLL HAUS", level: 1 })).toBeVisible();
  await expect(cashier.getByRole("button", { name: "Show QR code" })).toBeVisible();
  await expect(cashier.getByRole("heading", { name: "Team" })).toHaveCount(0);
  await expect(cashier.getByRole("form", { name: "Add bank account" })).toHaveCount(0);
  await cashierContext.close();
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
