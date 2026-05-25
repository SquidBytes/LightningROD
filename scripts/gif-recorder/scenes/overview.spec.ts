import { test, expect } from "@playwright/test";
import {
  gotoAndWait,
  hideCursor,
  scrollTo,
  settle,
  waitForCharts,
} from "./_helpers";

test("overview", async ({ page }) => {
  await gotoAndWait(page, "/");
  await hideCursor(page);

  await expect(
    page.getByRole("heading", { name: "Home", level: 1 }),
  ).toBeVisible({ timeout: 15_000 });
  await waitForCharts(page, 2);
  await settle(page, 800);

  await scrollTo(page, 0.45, 1800);
  await scrollTo(page, 1, 1800);
  await scrollTo(page, 0, 1500);
});
