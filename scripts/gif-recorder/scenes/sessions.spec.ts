import { test, expect } from "@playwright/test";
import {
  gotoAndWait,
  hideCursor,
  scrollTo,
  settle,
  waitForHtmxSettle,
} from "./_helpers";

test("sessions", async ({ page }) => {
  await gotoAndWait(page, "/charging/sessions");
  await hideCursor(page);

  // Wait for the session table to render at least a few rows.
  await expect(page.locator("tr[hx-get*='/charging/sessions/']").first()).toBeVisible({
    timeout: 15_000,
  });
  await settle(page, 800);

  // Show the table.
  await scrollTo(page, 0.2, 1200);

  // Click the third visible row to open the detail drawer.
  const rows = page.locator("tr[hx-get*='/charging/sessions/']");
  await rows.nth(2).click();
  await waitForHtmxSettle(page);
  await settle(page, 1500);

  // Show the drawer body content.
  await page.evaluate(() => {
    const drawer = document.getElementById("drawer-body");
    if (drawer) drawer.scrollTo({ top: drawer.scrollHeight * 0.5, behavior: "smooth" });
  });
  await settle(page, 1200);
  await page.evaluate(() => {
    const drawer = document.getElementById("drawer-body");
    if (drawer) drawer.scrollTo({ top: 0, behavior: "smooth" });
  });
  await settle(page, 1000);

  // Close drawer (overlay click). Two overlays exist (sidebar + session
  // drawer); scope to the session-drawer one by `for=` attribute.
  await page.locator("label[for='session-drawer']").click({ force: true });
  await settle(page, 1000);
});
