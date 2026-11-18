FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    PORT=3020 \
    HOST=127.0.0.1 \
    NEXT_TELEMETRY_DISABLED=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg librsvg2-bin && \
    rm -rf /var/lib/apt/lists/* && \
    corepack enable && corepack prepare pnpm@latest --activate

COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml* ./
RUN pnpm install || npm install

COPY . .

RUN pnpm build || npx next build

EXPOSE 3020

CMD ["sh", "-c", "exec npx next start -p ${PORT:-3020} -H ${HOST:-127.0.0.1}"]
