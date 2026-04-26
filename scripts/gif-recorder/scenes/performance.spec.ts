import { test } from "@playwright/test";
import {
  gotoAndWait,
  hideCursor,
  scrollTo,
  settle,
  waitForCharts,
} from "./_helpers";

test("performance", async ({ page }) => {
  // Default range is "all" already — no preset click needed.
  await gotoAndWait(page, "/charging/performance");
  await hideCursor(page);

  await waitForCharts(page, 1);
  await settle(page, 1000);

  await scrollTo(page, 0.45, 1500);
  await scrollTo(page, 0.9, 1500);
  await scrollTo(page, 0, 1200);
});
