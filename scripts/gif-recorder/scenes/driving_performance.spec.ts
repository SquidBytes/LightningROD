import { test } from "@playwright/test";
import {
  clickPreset,
  gotoAndWait,
  hideCursor,
  scrollTo,
  settle,
  waitForCharts,
} from "./_helpers";

test("driving_performance", async ({ page }) => {
  await gotoAndWait(page, "/driving/performance");
  await hideCursor(page);

  // Click "All" defensively in case this view shares the trip range default.
  await clickPreset(page, "All").catch(() => {});
  await waitForCharts(page, 1).catch(() => {});
  await settle(page, 1000);

  await scrollTo(page, 0.45, 1500);
  await scrollTo(page, 0.9, 1500);
  await scrollTo(page, 0, 1200);
});
