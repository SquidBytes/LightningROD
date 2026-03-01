# :lucide-paintbrush: CSS and Styling

LightningROD uses Tailwind CSS v4 with the standalone CLI. There is no Node.js dependency.

## How It Works

- Source styles are in `input.css`
- Compiled output is committed at `static/css/output.css`
- The Tailwind binary is gitignored (download it locally when needed)

The compiled CSS is committed so that Docker builds and fresh clones work without running the Tailwind CLI.

## Rebuilding

After modifying templates or adding new Tailwind classes:

```bash
./tailwindcss -i input.css -o static/css/output.css --minify
```

For development with auto-rebuild on file changes:

```bash
./tailwindcss -i input.css -o static/css/output.css --watch
```

## Getting the Tailwind CLI

Download the standalone binary for your platform:

=== "Linux (x64)"

    ```bash
    curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64
    chmod +x tailwindcss-linux-x64
    mv tailwindcss-linux-x64 tailwindcss
    ```

=== "macOS (Apple Silicon)"

    ```bash
    curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-macos-arm64
    chmod +x tailwindcss-macos-arm64
    mv tailwindcss-macos-arm64 tailwindcss
    ```

=== "macOS (Intel)"

    ```bash
    curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-macos-x64
    chmod +x tailwindcss-macos-x64
    mv tailwindcss-macos-x64 tailwindcss
    ```

## Tailwind v4 Notes

This project uses Tailwind v4, which has a different configuration syntax than v3:

```css title="input.css"
--8<-- "input.css"
```

!!! warning
    Tailwind v4 uses `@import "tailwindcss"` instead of the v3 `@tailwind base/components/utilities` directives. If you see documentation for v3 syntax, it won't work here.

## Dark Mode

The UI uses Tailwind's dark color scheme by default. The `base.html` template sets the dark background, and all component styles use dark-appropriate colors (slate grays, muted text, etc.).
