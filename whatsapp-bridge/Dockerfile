FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    ca-certificates \
    fonts-liberation \
    patch \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY patches ./patches
RUN patch -p1 -d node_modules/whatsapp-web.js < patches/whatsapp-web.js-1.34.7-lid.patch
COPY src ./src

RUN mkdir -p /app/session && chown -R node:node /app
USER node

CMD ["node", "src/index.js"]
