import { test } from "@playwright/test";
import { gotoAndWait, hideCursor, scrollTo, settle } from "./_helpers";

test("settings", async ({ page }) => {
  await gotoAndWait(page, "/settings");
  await hideCursor(page);
  await settle(page, 1200);

  // Slow scroll-tour to show all settings sections.
  await scrollTo(page, 0.25, 1500);
  await scrollTo(page, 0.55, 1500);
  await scrollTo(page, 0.85, 1500);
  await scrollTo(page, 1, 1200);
  await scrollTo(page, 0, 1500);
});
