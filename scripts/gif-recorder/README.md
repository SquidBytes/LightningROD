# gif-recorder

Scripted GIFs for the README/docs gallery. Each scene is a Playwright test that
drives the running app, Playwright records video, and `ffmpeg` palettizes it
into an optimized GIF.

## Quick start

```bash
# 1. dev server + seed data already running, e.g.:
docker compose -f docker-compose.yml -f docker/docker-compose.dev.yml up db -d
uv run alembic upgrade head
uv run python -m scripts.seed.main --all
uv run uvicorn web.main:app --port 8000

# 2. record every scene for the release you are about to tag
GIF_TAG=v0.4.0 ./scripts/gif-recorder/record.sh

# 3. final gifs land in scripts/gif-recorder/output/
ls scripts/gif-recorder/output/
```

Outputs: `output/lr_<scene>-<tag>.gif`, for example
`output/lr_overview-v0.4.0.gif`. `scripts/tag-release.sh <version>` publishes
matching `v<version>` GIFs into `docs/assets/images/` under stable names like
`lr_overview.gif`, then refreshes versioned GIF captions in markdown.

If you only want to publish already-recorded GIFs without tagging a release:

```bash
./scripts/gif-recorder/publish-gifs.sh --tag v0.4.0
```

## Layout

```
scripts/gif-recorder/
  playwright.config.ts     # 1920x1080 viewport, video=on, headless
  record.sh                # entrypoint: pre-flight check, run tests, convert
  convert-to-gif.sh        # 2-pass ffmpeg palette + paletteuse
  scenes/
    _helpers.ts            # settle/hideCursor/gotoAndWait shared helpers
    overview.spec.ts       # one file per scene
  output/                  # final gifs land here (gitignored)
```

## Adding a scene

1. Create `scenes/<name>.spec.ts`.
2. Use the helpers — `gotoAndWait`, `hideCursor`, `settle(ms)` — to keep
   pacing readable.
3. Output filename is `lr_<name>-<tag>.gif`, derived from the spec filename
   and `GIF_TAG`.
4. `GIF_TAG=v0.4.0 ./record.sh <name>` records just that one.

## Tuning the GIF

`convert-to-gif.sh` defaults to `fps=15`, `width=960`. Override per scene by
passing args, or fork the script. Trade-offs:

| Knob | Smaller file | Smoother |
|------|--------------|----------|
| fps  | 10–12        | 20–24    |
| width| 720–800      | 1080+    |
| dither | `bayer:bayer_scale=5` (current) | `floyd_steinberg` (cleaner gradients, larger) |

Plotly charts have soft gradients — if banding is ugly, swap the dither.

## Env vars

| var | default | what it does |
|-----|---------|--------------|
| `GIF_PORT` | `8000` | Dev server port |
| `GIF_BASE_URL` | `http://localhost:$GIF_PORT` | Full base URL override |
| `GIF_OUT_DIR` | `scripts/gif-recorder/output` | Where final gifs land |
| `GIF_KEEP_RAW` | `0` | Set `1` to keep raw `.webm` videos for debugging |
| `GIF_FPS` | `15` | Frame rate of the final GIF |
| `GIF_WIDTH` | `1280` | Output width (height auto-scaled from viewport aspect) |
| `GIF_HEAD_TRIM` | `1.5` | Seconds to drop from the front of the video. Playwright video starts at context creation, so the first ~1.5s is page-render settle time. |
| `GIF_TAG` | current branch | Suffix appended to output filenames. Set to `v<release>` before running `tag-release.sh`; set to empty to disable. |
| `GIF_VIEWPORT_WIDTH` | `1920` | Browser viewport width (also the video width — must match) |
| `GIF_VIEWPORT_HEIGHT` | `1080` | Browser viewport height (also the video height — must match) |

The defaults give 1080p source video downscaled to 1280px-wide GIFs (16:9
aspect, ~720p). That mirrors the layout breathing room of a real desktop
deployment — the dashboard's sidebar/chart columns won't feel cramped, and
there's room for sidebars/drawers to expand later. If you need a tighter
file size, drop `GIF_WIDTH` to `960` for that scene.

> ⚠️ Viewport gotcha: Playwright's `devices["Desktop Chrome"]` preset
> includes its own `viewport: 1280×720` and silently clobbers any viewport
> set at the top-level `use:` block when spread into a project. The config
> here intentionally avoids that preset and pins viewport explicitly —
> otherwise the recorded video has an 80px gray dead band at the bottom
> where the viewport doesn't reach. If you ever add a new project,
> set `viewport` on it directly rather than spreading a device.

Per-scene overrides go in `scenes/<name>.env` and are sourced before
conversion. Recognized keys: `FPS`, `WIDTH`, `HEAD_TRIM`. Example for a
session-list page that needs more head trim:

```bash
# scripts/gif-recorder/scenes/sessions.env
HEAD_TRIM=2.0
WIDTH=1080
```

## Notes for future E2E work

This Playwright setup is intentionally separate from any future `tests/e2e/`
suite — different goals (visual storytelling vs. behavior assertions), but
shared infrastructure. The patterns proven out here that should carry over:

**Selectors.** This codebase doesn't yet have `data-testid` attributes — the
overview scene leans on the `Home` page heading and a DOM query for
`.plotly`, which are fragile. For E2E, add `data-testid` to
the canonical surfaces (vehicle card, stat tile, chart container, primary
nav links, drawer root, modal root, form submit buttons) and use
`page.getByTestId(...)` everywhere. The gif-recorder can then drop its
text-based fallbacks too.

**Wait strategy.** Do not rely on `networkIdle` alone — Plotly and HTMX both
stay quiet while still hydrating. The pattern that actually works:

1. `getByTestId('<page-marker>').toBeVisible()` — confirms the route
   rendered.
2. `page.waitForFunction(() => document.querySelectorAll('.plotly').length >= N)`
   — confirms charts hydrated. (Or wait for a custom `data-loaded="true"`
   attribute set by the chart helper.)
3. A small `settle(300)` pad before screenshotting/asserting.

**HTMX.** When clicking an HTMX trigger, await `htmx:afterSettle` before
asserting on the swapped fragment:

```ts
await page.evaluate(() => new Promise<void>((r) => {
  document.body.addEventListener('htmx:afterSettle', () => r(), { once: true });
}));
```

Wrap that in `_helpers.ts` as `waitForHtmxSettle(page)` once E2E starts.

**Fixtures.** Both the gif-recorder and E2E can share a `seed_sample.py`
snapshot — the gif-recorder needs *enough* data to look good; E2E needs
*deterministic* counts. The current sample seed produces stable totals
(268 sessions, 173 trips, $166.11 total cost) so it can serve both, as long
as nothing rewrites it mid-test. For E2E, wrap the run in a per-test DB
truncate-and-reseed (or transaction rollback) so tests don't depend on
order.

**Config split.** When E2E lands, keep two configs:

| | gif-recorder | E2E |
|---|---|---|
| `video` | `on` (we want it) | `retain-on-failure` |
| `headless` | `true` | `true` (CI) / `false` (debug) |
| `fullyParallel` | `false` (deterministic pacing) | `true` |
| `retries` | `0` | `1–2` (CI flake guard) |
| `reporter` | `list` | `html` + `github` (in CI) |
| viewport | `1280×800` (matches existing gifs) | `1280×720` or `1440×900` |

**Helpers to promote.** When you start E2E, lift `_helpers.ts` to
`tests/e2e/helpers/` (or a top-level `tests/helpers/`) and import from
both. `settle`, `gotoAndWait`, and a future `waitForHtmxSettle` are
useful in both contexts. `hideCursor` is gif-only.

**Browser install.** `@playwright/test` is already a dep, but Playwright
needs browsers installed separately. If `npx playwright test` complains
about missing chromium, run `npx playwright install chromium`. CI: add
`npx playwright install --with-deps chromium` to the workflow.

**FastAPI dev server in CI.** For E2E in CI, use Playwright's `webServer`
config block to spawn the app:

```ts
webServer: {
  command: "uv run uvicorn web.main:app --port 8000",
  url: "http://localhost:8000",
  reuseExistingServer: !process.env.CI,
  timeout: 120_000,
},
```

The gif-recorder skips this and expects a server already running, because
the human is usually iterating on UI between recordings.
