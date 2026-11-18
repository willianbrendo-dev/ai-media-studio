FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    PORT=3010 \
    HOST=0.0.0.0 \
    CI=true

# Install system dependencies: ffmpeg, build tools for native addons (sqlite3/sharp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    python3 \
    make \
    g++ \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency manifests
COPY package*.json ./

# Install production dependencies
RUN npm install --omit=dev

# Copy application source code
COPY . .

# Create runtime directories
RUN mkdir -p /app/data /app/uploads

EXPOSE 3010

CMD ["node", "index.js"]
