# Next.js standalone server. Build from web/: docker build .
FROM node:24-bookworm-slim AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Rewrites are fixed at build time. In production Caddy sends /v1 to the API directly,
# so this only matters if the web container is reached on its own.
ARG BUSTRUST_API_URL=http://api:8000
ENV BUSTRUST_API_URL=$BUSTRUST_API_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-bookworm-slim
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /web/public ./public
COPY --from=build --chown=node /web/.next/standalone ./
COPY --from=build --chown=node /web/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
