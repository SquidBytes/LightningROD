# CSS and Styling

LightningROD uses Tailwind CSS v4 with DaisyUI v5 as the component library. CSS is built via npm during the Docker build.

## How It Works

- Source styles are in `input.css`
- DaisyUI is configured as a Tailwind plugin with the `dark` theme as default
- Compiled output goes to `web/static/css/output.css`
- The Docker build compiles CSS in a Node 22 stage, then copies it into the Python runtime stage

```css title="input.css"
@import "tailwindcss";
@plugin "daisyui" {
  themes: dark --default;
}

@theme {
  --color-brand-accent: #47A8E5;  /* Lightning Blue */
  --color-accent: #47A8E5;
}
```

## Build Pipeline

The Dockerfile uses a multi-stage build:

1. **Stage 1** (`node:22-slim`): Installs npm dependencies, compiles Tailwind + DaisyUI into `output.css`
2. **Stage 2** (`python:3.11-slim`): Runs the FastAPI application, copies compiled CSS from stage 1

This means Tailwind v4 and DaisyUI v5 require Node 20+ for compilation, but the runtime container has no Node dependency.

## Local Development

Install npm dependencies and run the Tailwind CLI:

```bash
npm install
npx @tailwindcss/cli -i input.css -o web/static/css/output.css --watch
```

## DaisyUI Components

DaisyUI v5 provides CSS-only components. Common classes used throughout:

| Component | Class | Usage |
|-----------|-------|-------|
| Buttons | `btn`, `btn-primary`, `btn-ghost` | Actions, navigation |
| Badges | `badge`, `badge-sm` | Status indicators, network colors |
| Cards | `card`, `card-body` | Content containers |
| Tables | `table`, `table-zebra` | Data tables |
| Tabs | `tabs`, `tab` | Modal sections, settings tabs |
| Modals | `modal`, `modal-box` | Edit forms, confirmations |
| Drawers | `drawer`, `drawer-side` | Sidebar, session detail |
| Stats | `stats`, `stat` | Metric displays |
| Form controls | `input`, `select`, `checkbox` | Form fields |

Since DaisyUI is CSS-only (zero JavaScript), components work correctly after HTMX partial swaps without re-initialization.

## Brand Colors

The LightningROD palette is defined in `input.css`:

| Variable | Color | Name |
|----------|-------|------|
| `--color-brand-darkest` | `#081534` | Maastricht Blue |
| `--color-brand-dark` | `#133A7C` | Dark Cerulean |
| `--color-brand-mid` | `#2A6BAC` | Lapis Lazuli |
| `--color-brand-accent` | `#47A8E5` | Lightning Blue |
| `--color-brand-silver` | `#C6C6C6` | Silver Sand |

## Dark Mode

The app defaults to DaisyUI's `dark` theme. The `base.html` template and all components use dark-appropriate colors throughout.

## Plotly CSS Isolation

Tailwind v4's CSS resets can break Plotly's internal SVG rendering. The `input.css` file includes isolation rules that restore default styles inside `.js-plotly-plot` containers.
