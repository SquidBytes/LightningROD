## Stage 1: Build CSS with Node 22 (Tailwind v4 + DaisyUI v5 require Node 20+)
FROM node:25-slim AS css-builder

WORKDIR /build
COPY package.json package-lock.json* ./
RUN npm install
COPY input.css ./
COPY web/templates/ web/templates/
COPY web/static/ web/static/
RUN npx @tailwindcss/cli -i input.css -o web/static/css/output.css --minify

## Stage 2: Python application
FROM python:3.14-slim

# Version passed in from docker-compose (or `docker build --build-arg`).
# Defaults to "dev" for ad-hoc builds. Mirror into ENV so the running app
# can read it at runtime via os.environ["LIGHTNINGROD_VERSION"].
ARG LIGHTNINGROD_VERSION=dev
ENV LIGHTNINGROD_VERSION=${LIGHTNINGROD_VERSION}

LABEL org.opencontainers.image.title="LightningROD" \
      org.opencontainers.image.description="Self-hosted charging analytics for Ford electric vehicles" \
      org.opencontainers.image.version="${LIGHTNINGROD_VERSION}" \
      org.opencontainers.image.source="https://github.com/aminorjourney/LightningROD"

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

RUN chmod +x docker/entrypoint.sh

EXPOSE 8000

# Liveness + readiness via /healthz (returns 503 if the DB is unreachable).
# Uses Python stdlib so no extra packages are needed in the slim image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)" || exit 1

ENTRYPOINT ["./docker/entrypoint.sh"]
