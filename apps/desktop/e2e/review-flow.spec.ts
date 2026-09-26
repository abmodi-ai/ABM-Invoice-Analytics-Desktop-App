import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

const creds = JSON.parse(readFileSync(join(here, "../../../.verismo-dev/demo-credentials.json"), "utf8"));
const shots = join(here, "../../../tests/e2e/out");

test("sign in, dashboard, review with keyboard, reports", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByLabel("Username").fill(creds.username);
  await page.getByLabel("Password").fill(creds.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByText("Invoices with hard or probable flags, by invoice month")).toBeVisible();
  await page.screenshot({ path: `${shots}/01-dashboard.png`, fullPage: true });

  await page.getByRole("link", { name: "Review queue" }).click();
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible();
  await expect(page.getByText("Side by side")).toBeVisible();
  await page.screenshot({ path: `${shots}/02-review.png`, fullPage: false });
  const firstId = await page.getByRole("listbox", { name: "Flags" }).getByRole("option").first().getAttribute("aria-selected");
  expect(firstId).toBe("true");

  // keyboard: J moves to the second flag, C opens the decision panel, Submit records it
  await page.keyboard.press("j");
  await expect(page.getByRole("listbox", { name: "Flags" }).getByRole("option").nth(1)).toHaveAttribute("aria-selected", "true");
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("c");
  await expect(page.getByRole("button", { name: /Submit/ })).toBeVisible();
  await page.screenshot({ path: `${shots}/03-decision.png` });
  await page.getByRole("button", { name: /Submit/ }).click();
  await expect(page.getByRole("button", { name: /Submit/ })).toBeHidden();

  await page.getByRole("link", { name: "Invoices" }).click();
  await expect(page.getByRole("heading", { name: "Invoices" })).toBeVisible();
  await page.getByRole("link", { name: "Rules" }).click();
  await expect(page.getByText("Detection rules")).toBeVisible();
  await page.screenshot({ path: `${shots}/04-rules.png`, fullPage: true });
  await page.getByRole("link", { name: "Reports" }).click();
  await page.getByRole("tab", { name: "Rule precision" }).click();
  await expect(page.getByText("INV-001").first()).toBeVisible();
  await page.getByRole("link", { name: "Audit log" }).click();
  await page.getByRole("button", { name: "Check integrity" }).click();
  await expect(page.getByText(/Chain intact/)).toBeVisible();
  await page.getByRole("link", { name: "Ingest" }).click();
  await page.screenshot({ path: `${shots}/05-ingest.png` });
  await page.getByRole("link", { name: "Parties & patients" }).click();
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByText("Local AI")).toBeVisible();
  expect(errors).toEqual([]);
});
