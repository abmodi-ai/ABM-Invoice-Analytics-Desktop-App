import { expect, test } from "@playwright/test";

test("select invoices on the list and delete them after confirming", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByRole("link", { name: "Invoices" }).click();
  await expect(page.getByRole("heading", { name: "Invoices" })).toBeVisible();
  const subtitle = page.getByText(/^[\d,]+ matching$/);
  const before = Number((await subtitle.textContent())!.replace(/\D/g, ""));
  const del = page.getByRole("button", { name: /Delete selected/ });
  await expect(del).toBeDisabled();

  const rows = page.locator("tbody tr");
  const firstNumber = (await rows.nth(0).getByRole("link").textContent())!.trim();
  await rows.nth(0).getByRole("checkbox").check();
  await rows.nth(1).getByRole("checkbox").check();
  await expect(del).toHaveText(/Delete selected \(2\)/);

  // cancelling deletes nothing
  await del.click();
  const dialog = page.getByRole("dialog", { name: "Delete 2 invoices?" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(firstNumber, { exact: false })).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  await expect(subtitle).toHaveText(`${before.toLocaleString()} matching`);

  await del.click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete 2 invoices" }).click();
  await expect(page.getByRole("status")).toHaveText(/Deleted 2 invoices/);
  await expect(subtitle).toHaveText(`${(before - 2).toLocaleString()} matching`);
  await expect(del).toBeDisabled();

  // select-all on the page selects every row
  await page.getByLabel("Select all invoices on this page").check();
  await expect(del).toHaveText(/Delete selected \(\d+\)/);
  expect(errors).toEqual([]);
});

test("delete one invoice from its own page", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByRole("link", { name: "Invoices" }).click();
  const subtitle = page.getByText(/^[\d,]+ matching$/);
  const before = Number((await subtitle.textContent())!.replace(/\D/g, ""));
  await page.locator("tbody tr").nth(0).getByRole("link").click();
  await page.getByRole("button", { name: "Delete invoice" }).click();
  const dialog = page.getByRole("dialog", { name: "Delete this invoice?" });
  await expect(dialog.getByText(/permanently deleted/)).toBeVisible();
  await dialog.getByRole("button", { name: "Delete invoice" }).click();
  await expect(page.getByRole("heading", { name: "Invoices" })).toBeVisible();
  await expect(subtitle).toHaveText(`${(before - 1).toLocaleString()} matching`);
  expect(errors).toEqual([]);
});
