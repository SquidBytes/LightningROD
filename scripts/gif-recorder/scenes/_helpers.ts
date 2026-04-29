import { Page, expect } from "@playwright/test";

export async function settle(page: Page, ms = 600) {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(ms);
}

export async function hideCursor(page: Page) {
  await page.addStyleTag({
    content: `* { cursor: none !important; }`,
  });
}

export async function gotoAndWait(page: Page, path: string) {
  await page.goto(path);
  await settle(page);
}

export async function expectVisible(page: Page, selector: string) {
  await expect(page.locator(selector).first()).toBeVisible({ timeout: 10_000 });
}

/**
 * Wait for N Plotly charts to actually paint their SVG. The chart container
 * div appears in the DOM before Plotly hydrates — checking for `.plotly`
 * count alone leaves blank frames.
 */
export async function waitForCharts(page: Page, count = 1, timeoutMs = 15_000) {
  await page.waitForFunction(
    (n) => {
      const containers = Array.from(
        document.querySelectorAll<HTMLElement>(".plotly"),
      );
      return (
        containers.length >= n &&
        containers
          .slice(0, n)
          .every(
            (el) =>
              el.querySelector(".main-svg") !== null && el.offsetHeight > 50,
          )
      );
    },
    count,
    { timeout: timeoutMs },
  );
}

/**
 * Click a date-preset button (7d / 30d / 90d / YTD / 1y / All) on any page
 * that renders `partials/filter_bar.html`. Most seed-driven scenes want
 * "All" so older sample data is visible.
 */
export async function clickPreset(page: Page, label: string) {
  const btn = page
    .locator(".filter-preset-btn", { hasText: new RegExp(`^${label}$`, "i") })
    .first();
  await btn.click();
  await waitForHtmxSettle(page);
  await settle(page, 400);
}

/**
 * HTMX dispatches `htmx:afterSettle` on the body after a swap finishes.
 * This is more reliable than `networkidle` for HTMX-driven UIs because
 * the request can resolve before the DOM swap completes.
 */
export async function waitForHtmxSettle(page: Page, timeoutMs = 5_000) {
  await page
    .evaluate(
      (t) =>
        new Promise<void>((resolve) => {
          const done = () => resolve();
          document.body.addEventListener("htmx:afterSettle", done, {
            once: true,
          });
          setTimeout(done, t);
        }),
      timeoutMs,
    )
    .catch(() => {});
}

/**
 * Smooth-scroll to a fraction of total page height (0..1). 0 = top, 1 = bottom.
 */
export async function scrollTo(page: Page, fraction: number, settleMs = 1500) {
  await page.evaluate((f) => {
    window.scrollTo({
      top: document.documentElement.scrollHeight * f,
      behavior: "smooth",
    });
  }, fraction);
  await settle(page, settleMs);
}
