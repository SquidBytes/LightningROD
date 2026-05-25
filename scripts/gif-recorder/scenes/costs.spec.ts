import { test } from "@playwright/test";
import {
  gotoAndWait,
  hideCursor,
  scrollTo,
  settle,
  waitForCharts,
  waitForHtmxSettle,
} from "./_helpers";

test("costs", async ({ page }) => {
  await gotoAndWait(page, "/charging/costs");
  await hideCursor(page);

  // Cost Explorer's monthly trend chart is the only full-size Plotly on the
  // page; wait for it to paint before recording.
  await waitForCharts(page, 1);
  await settle(page, 800);

  // Nudge so the top strip + control row + ledger all sit in frame.
  await scrollTo(page, 0.1, 1000);

  // AC quickselect → ledger narrows to AC networks (gracefully skip if the
  // seed has no AC sessions in the active range).
  const acBtn = page.locator('[data-quickselect-action="ac"]').first();
  if ((await acBtn.count()) > 0) {
    await acBtn.click();
    await waitForHtmxSettle(page);
    await settle(page, 1400);

    await page.locator('[data-quickselect-action="clear"]').first().click();
    await waitForHtmxSettle(page);
    await settle(page, 1000);
  }

  // Free-charging what-if → reveals the "Without free charging" sub-line in
  // the aside and re-bills the monthly trend chart.
  const freeToggle = page.locator('input[name="free_what_if"]');
  await freeToggle.check();
  await waitForHtmxSettle(page);
  await settle(page, 1500);

  await freeToggle.uncheck();
  await waitForHtmxSettle(page);
  await settle(page, 1000);

  // Click a ledger row → table collapses into the single-network detail panel.
  const firstRow = page.locator("[data-cost-explorer-scope-row]").first();
  if ((await firstRow.count()) > 0) {
    await firstRow.click();
    await waitForHtmxSettle(page);
    await settle(page, 1800);

    await page
      .locator("[data-cost-explorer-scope-clear]")
      .first()
      .click();
    await waitForHtmxSettle(page);
    await settle(page, 1200);
  }

  // Scroll down to show the Savings Scenarios card below the explorer, then
  // back to the top.
  await scrollTo(page, 0.85, 1500);
  await scrollTo(page, 0, 1200);
});
