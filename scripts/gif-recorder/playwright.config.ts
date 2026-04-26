import { defineConfig } from "@playwright/test";

const PORT = Number(process.env.GIF_PORT ?? 8000);
const BASE_URL = process.env.GIF_BASE_URL ?? `http://localhost:${PORT}`;
const VIEWPORT_WIDTH = Number(process.env.GIF_VIEWPORT_WIDTH ?? 1920);
const VIEWPORT_HEIGHT = Number(process.env.GIF_VIEWPORT_HEIGHT ?? 1080);

// NOTE: Do NOT spread `devices["Desktop Chrome"]` into the project — its
// preset viewport (1280×720) silently overrides the viewport set here.
// We pin viewport explicitly so the video size and the rendered page area
// match exactly (otherwise the recorded video has a dead band at the
// bottom where the viewport doesn't reach).
export default defineConfig({
  testDir: "./scenes",
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "./output/raw",
  timeout: 60_000,
  use: {
    baseURL: BASE_URL,
    headless: true,
    viewport: { width: VIEWPORT_WIDTH, height: VIEWPORT_HEIGHT },
    deviceScaleFactor: 1,
    video: {
      mode: "on",
      size: { width: VIEWPORT_WIDTH, height: VIEWPORT_HEIGHT },
    },
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
