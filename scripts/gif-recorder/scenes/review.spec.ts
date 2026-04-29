import { test } from "@playwright/test";
import { gotoAndWait, hideCursor, scrollTo, settle } from "./_helpers";

test("review", async ({ page }) => {
  // The review tabs are query-string based — `/review/networks` etc. are
  // HTMX fragment endpoints that return unstyled partials. Use the
  // `?tab=...&sub=...` URLs that the actual nav links go to.
  await gotoAndWait(page, "/review?tab=pending&sub=networks");
  await hideCursor(page);
  await settle(page, 1500);
  await scrollTo(page, 0.3, 1200);
  await scrollTo(page, 0, 800);

  await page.goto("/review?tab=pending&sub=locations");
  await settle(page, 1500);
  await scrollTo(page, 0.3, 1200);
  await scrollTo(page, 0, 800);

  await page.goto("/review?tab=approved");
  await settle(page, 1500);
  await scrollTo(page, 0.3, 1200);
  await scrollTo(page, 0, 800);
});
