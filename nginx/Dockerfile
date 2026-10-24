# Build context must be the repo root: docker build -f nginx/Dockerfile .
FROM node:20-alpine AS builder

WORKDIR /build

# Install root-level deps (concurrently)
COPY package.json package-lock.json ./
RUN npm ci

# Install each frontend's deps with its own lockfile
COPY frontend/host/package.json frontend/host/package-lock.json ./frontend/host/
COPY frontend/player/package.json frontend/player/package-lock.json ./frontend/player/
COPY frontend/admin/package.json frontend/admin/package-lock.json ./frontend/admin/
RUN npm ci --prefix frontend/host && \
    npm ci --prefix frontend/player && \
    npm ci --prefix frontend/admin

# Copy source and build all three apps
COPY frontend/ ./frontend/
RUN npm run build

FROM nginx:1.25-alpine
COPY --from=builder /build/frontend/host/dist   /usr/share/nginx/html/host
COPY --from=builder /build/frontend/player/dist /usr/share/nginx/html/player
COPY --from=builder /build/frontend/admin/dist  /usr/share/nginx/html/admin
COPY nginx/nginx.prod.conf /etc/nginx/nginx.conf
