FROM python:3.11-slim

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install uv

# Layer: dependencies (cached separately from app code)
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

# Install npm deps for Tailwind + DaisyUI build
COPY package.json package-lock.json* ./
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/*
RUN npm install

# Layer: application code
COPY . .

# Compile Tailwind CSS with DaisyUI
RUN npx @tailwindcss/cli -i input.css -o web/static/css/output.css --minify

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
