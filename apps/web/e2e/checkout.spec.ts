import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { apiEnv } from "../playwright.config";
import { totp } from "./helpers";

type Seed = { email: string; password: string; totp_secret: string; slug: string; merchant_id: string };

function seed(): Seed {
  const out = execFileSync("uv", ["run", "python", "scripts/e2e_seed.py"], {
    cwd: path.resolve(__dirname, "../../api"),
    env: { ...process.env, ...apiEnv },
    encoding: "utf8",
  });
  return JSON.parse(out.trim().split("\n").pop()!);
}

let shop: Seed;
test.beforeAll(() => {
  shop = seed();
});

async function payAtCounter(page: import("@playwright/test").Page, amount: string) {
  await page.goto(`/pay/${shop.slug}`);
  await expect(page.getByRole("heading", { name: "E2E Shop" })).toBeVisible();
  await page.getByLabel("Amount (NZD)").fill(amount);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("What happens next")).toBeVisible();
  await page.getByRole("button", { name: /^Pay \$.* with my bank$/ }).click();
  await expect(page.getByText("MOCK BANK · test only")).toBeVisible();
}

test("counter QR: pay, approve at the bank, see the bank-confirmed result", async ({ page }) => {
  await payAtCounter(page, "12.50");
  await expect(page.getByText("$12.50")).toBeVisible();
  await page.getByRole("button", { name: "Approve payment" }).click();
  await expect(page).toHaveURL(/\/c\/[\w-]+\/return$/);
  await expect(page.getByText("Paid · confirmed by your bank")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "$12.50" })).toBeVisible();
});

test("declined payment explains what happened and offers to try again", async ({ page }) => {
  await payAtCounter(page, "5.15");
  await page.getByRole("button", { name: "Approve payment" }).click();
  await expect(page.getByRole("heading", { name: "Your payment didn't go through" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "Pay $5.15 with my bank" })).toBeEnabled();
});

test("cancelling at the bank lets the customer pay again", async ({ page }) => {
  await payAtCounter(page, "7.00");
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("heading", { name: "You cancelled the payment" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "Pay $7.00 with my bank" }).click();
  await page.getByRole("button", { name: "Approve payment" }).click();
  await expect(page.getByText("Paid · confirmed by your bank")).toBeVisible({ timeout: 15_000 });
});

test("unclear bank result tells the customer not to pay again", async ({ page }) => {
  await payAtCounter(page, "5.04");
  await page.getByRole("button", { name: "Approve payment" }).click();
  await expect(page.getByRole("heading", { name: "We can't confirm this payment yet" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Please don't pay again.")).toBeVisible();
  await expect(page.getByRole("button", { name: /with my bank/ })).toHaveCount(0);
});

test("slow bank shows processing, never paid, until confirmed", async ({ page }) => {
  await payAtCounter(page, "5.20");
  await page.getByRole("button", { name: "Approve payment" }).click();
  await expect(page.getByText("Processing · not paid yet")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Paid · confirmed by your bank")).toHaveCount(0);
});

test("merchant takes a payment from the dashboard and sees it paid", async ({ page, context }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(shop.email);
  await page.getByLabel("Password").fill(shop.password);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("6-digit code").fill(totp(shop.totp_secret));
  await page.getByRole("button", { name: "Log in" }).click();
  // Launch mode: the only business opens directly.
  await expect(page.getByRole("heading", { name: "E2E Shop", level: 1 })).toBeVisible();

  await expect(page.getByText("E2E Shop ··2300")).toBeVisible();
  await page.getByLabel("Amount (NZD)").fill("8.50");
  await page.getByRole("button", { name: "Show QR code" }).click();
  await expect(page.getByRole("img", { name: "QR code to pay $8.50" })).toBeVisible();
  await expect(page.getByTestId("request-status")).toHaveText("Waiting for customer");

  // The customer scans: open the checkout link on "their phone".
  const href = await page.getByRole("link", { name: "Open the checkout link" }).getAttribute("href");
  const phone = await context.newPage();
  await phone.goto(href!);
  await phone.getByRole("button", { name: "Pay $8.50 with my bank" }).click();
  await phone.getByRole("button", { name: "Approve payment" }).click();
  await expect(phone.getByText("Paid · confirmed by your bank")).toBeVisible({ timeout: 15_000 });

  await expect(page.getByTestId("request-status")).toHaveText("Paid", { timeout: 15_000 });
});
