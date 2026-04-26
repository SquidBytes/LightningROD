import { test } from "@playwright/test";
import {
  clickPreset,
  gotoAndWait,
  hideCursor,
  scrollTo,
  settle,
  waitForCharts,
} from "./_helpers";

test("battery", async ({ page }) => {
  // Default range is 7d — empty against sample seed (last data 2026-03-10),
  // so click "All" to show the full battery telemetry history.
  await gotoAndWait(page, "/battery");
  await hideCursor(page);

  await clickPreset(page, "All");
  await waitForCharts(page, 1);
  await settle(page, 1000);

  await scrollTo(page, 0.4, 1500);
  await scrollTo(page, 0.9, 1500);
  await scrollTo(page, 0, 1200);
});
