import { test, expect } from "@playwright/test";
import {
  clickPreset,
  gotoAndWait,
  hideCursor,
  scrollTo,
  settle,
} from "./_helpers";

test("trips", async ({ page }) => {
  // /driving/sessions defaults to 30d — empty against seed, switch to All.
  await gotoAndWait(page, "/driving/sessions");
  await hideCursor(page);

  await clickPreset(page, "All");
  await expect(page.locator("table tbody tr").first()).toBeVisible({
    timeout: 15_000,
  });
  await settle(page, 1000);

  await scrollTo(page, 0.3, 1500);
  await scrollTo(page, 0.8, 1500);
  await scrollTo(page, 0, 1200);
});
