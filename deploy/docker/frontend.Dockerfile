# Enterprise Fabric AI: the React UI (M17b; CLAUDE.md section 14).
#
#   docker build -f deploy/docker/frontend.Dockerfile -t enterprise-fabric/frontend:dev .
#
# Stage 1 builds frontend/dist with the exact npm lockfile (npm ci). Stage 2 serves it
# with an unprivileged nginx (uid 101, port 8080) that forwards /api/ to the API
# service (API_UPSTREAM, default http://api:8000; /metrics stays internal) and answers every
# other path with index.html, so client-side routes such as /admin work.

FROM node:24.22.3-alpine AS build
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
# Retries: a slow or interrupted registry connection (seen on the laptop: ECONNRESET after
# 35 minutes) should not fail the whole build.
RUN npm config set fetch-retries 5  && npm config set fetch-retry-mintimeout 20000  && npm config set fetch-retry-maxtimeout 120000  && npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29.8-alpine AS frontend
ENV API_UPSTREAM=http://api:8000
COPY deploy/docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /ui/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
