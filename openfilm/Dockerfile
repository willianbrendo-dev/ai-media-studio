FROM node:22-bookworm-slim

ENV OPENFILM_STUDIO_PORT=3005 \
    OPENFILM_NO_BROWSER=1 \
    ELECTRON_SKIP_BINARY_DOWNLOAD=1 \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=1 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    CI=true

# Install system dependencies: ffmpeg, git, curl, ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Enable corepack and pnpm
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate

WORKDIR /app

# Copy application source code
COPY . .

# Install dependencies (prepare will trigger UI build or prepare step)
RUN pnpm install --frozen-lockfile

# Explicitly ensure Studio UI is built
RUN pnpm --filter openfilm build:ui

EXPOSE 3005 3006

CMD ["node", "packages/openfilm/studio/server/main.mjs"]
