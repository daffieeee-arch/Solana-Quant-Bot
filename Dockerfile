FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS build
WORKDIR /app
RUN apt-get update \
  && apt-get install -y --no-install-recommends g++ make python3 \
  && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src ./src
COPY frontend ./frontend
RUN npm run build && npm prune --omit=dev && npm cache clean --force

FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3
ARG SOURCE_FREEZE_SHA256
RUN printf '%s' "$SOURCE_FREEZE_SHA256" | grep -Eq '^[0-9a-f]{64}$'
LABEL org.opencontainers.image.source-freeze.sha256="$SOURCE_FREEZE_SHA256"
# Fase-W/Provenance: immutable Git SHA + OCI revision-label (image-provenance).
ARG SOURCE_GIT_SHA
LABEL org.opencontainers.image.revision="$SOURCE_GIT_SHA"
ENV SOURCE_GIT_SHA="$SOURCE_GIT_SHA"
# ca-certificates: de native gRPC-connect (yellowstone) faalt met 'no native certs
# found' op bookworm-slim zonder de systeem-CA-store — de geyser-stream crasht de bot.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV NODE_ENV=production
RUN useradd --system --uid 10001 --create-home scanner
COPY package.json package-lock.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=build /app/frontend/dist ./frontend/dist
RUN mkdir /app/data && chown -R scanner:scanner /app
USER scanner
CMD ["node", "dist/main.js"]
