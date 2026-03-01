FROM python:3.11-slim

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install uv

# Layer: dependencies (cached separately from app code)
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

# Layer: application code
COPY . .

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
