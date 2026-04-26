import { test } from "@playwright/test";
import {
  clickPreset,
  gotoAndWait,
  hideCursor,
  scrollTo,
  settle,
  waitForCharts,
} from "./_helpers";

test("costs", async ({ page }) => {
  await gotoAndWait(page, "/charging/costs");
  await hideCursor(page);

  await waitForCharts(page, 1);
  await settle(page, 1000);

  // Show the cost summary tiles + chart at the top.
  await scrollTo(page, 0.35, 1500);

  // Switch to a tighter window to show the filter changing the chart, then
  // back to All.
  await clickPreset(page, "1y");
  await settle(page, 1500);
  await clickPreset(page, "All");
  await settle(page, 1500);

  await scrollTo(page, 0.85, 1500);
  await scrollTo(page, 0, 1200);
});
