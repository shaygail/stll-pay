import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

/**
 * End-to-end tests run the real API (mock payments, disposable database) and a
 * production build of the web app. No real money and no external services.
 */
const WEB_PORT = 3100;
const API_PORT = 8001;
export const OUTBOX = path.resolve(__dirname, "e2e/.outbox");

export const apiEnv = {
  ENVIRONMENT: "test",
  DATABASE_URL:
    process.env.E2E_DATABASE_URL ?? "postgresql+asyncpg://stllpay_app:devapp@localhost:5432/stllpay_e2e",
  MIGRATION_DATABASE_URL:
    process.env.E2E_MIGRATION_DATABASE_URL ??
    "postgresql+psycopg://stllpay_owner:devowner@localhost:5432/stllpay_e2e",
  EMAIL_BACKEND: "file",
  EMAIL_OUTBOX_DIR: OUTBOX,
  WEB_ORIGIN: `http://localhost:${WEB_PORT}`,
};

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "off",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command: `uv run python scripts/reset_test_db.py && uv run uvicorn app.main:app --port ${API_PORT}`,
      cwd: path.resolve(__dirname, "../api"),
      url: `http://localhost:${API_PORT}/ready`,
      env: apiEnv,
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: `npx next build && npx next start -p ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}`,
      env: { API_URL: `http://localhost:${API_PORT}`, NEXT_TELEMETRY_DISABLED: "1" },
      timeout: 300_000,
      reuseExistingServer: false,
    },
  ],
});
