## Stage 1: Build CSS with Node 22 (Tailwind v4 + DaisyUI v5 require Node 20+)
FROM node:22-slim AS css-builder

WORKDIR /build
COPY package.json package-lock.json* ./
RUN npm install
COPY input.css ./
COPY web/templates/ web/templates/
COPY web/static/ web/static/
RUN npx @tailwindcss/cli -i input.css -o web/static/css/output.css --minify

## Stage 2: Python application
FROM python:3.11-slim

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install uv

# Layer: dependencies (cached separately from app code)
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

# Layer: application code
COPY . .

# Copy compiled CSS from build stage
COPY --from=css-builder /build/web/static/css/output.css web/static/css/output.css

# Remove old static directory if present (pre-migration artifact)
RUN rm -rf static/css

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
