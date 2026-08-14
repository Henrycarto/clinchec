# syntax=docker/dockerfile:1.7
# Build context is the repo root — the app depends on packages/* via workspaces.

# ---------------------------------------------------------------------------
FROM node:22-alpine AS deps
WORKDIR /srv

COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/
COPY packages/shared-types/package.json ./packages/shared-types/
COPY packages/fhir-client/package.json ./packages/fhir-client/

RUN npm ci

# ---------------------------------------------------------------------------
# Local development — docker-compose mounts the source over this layer.
FROM node:22-alpine AS dev
WORKDIR /srv
ENV NODE_ENV=development
COPY --from=deps /srv/node_modules ./node_modules
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev", "--workspace", "apps/web"]

# ---------------------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /srv
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /srv/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* values are inlined at build time, so the image is environment
# specific. CI passes the target environment's URLs as build args.
ARG NEXT_PUBLIC_APP_URL
ARG NEXT_PUBLIC_SCAN_API_URL
ARG NEXT_PUBLIC_LIVE_API_URL
ARG NEXT_PUBLIC_FORMS_API_URL
ENV NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL \
    NEXT_PUBLIC_SCAN_API_URL=$NEXT_PUBLIC_SCAN_API_URL \
    NEXT_PUBLIC_LIVE_API_URL=$NEXT_PUBLIC_LIVE_API_URL \
    NEXT_PUBLIC_FORMS_API_URL=$NEXT_PUBLIC_FORMS_API_URL

RUN npm run build --workspace apps/web

# ---------------------------------------------------------------------------
FROM node:22-alpine AS runner
WORKDIR /srv

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

# `output: "standalone"` emits a self-contained server plus only the modules it
# actually traced, which is a fraction of the monorepo's node_modules.
COPY --from=builder --chown=nextjs:nodejs /srv/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /srv/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=nextjs:nodejs /srv/apps/web/public ./apps/web/public

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://localhost:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "apps/web/server.js"]
