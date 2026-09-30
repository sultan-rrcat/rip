FROM node:22-alpine AS build

WORKDIR /app

# Build-time proxy (ARG only, not persisted): npm reads env during RUN.
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
# Same-origin bake by default (empty). Override at build time to retarget:
# `docker compose build --build-arg VITE_API_URL=http://host:8000 frontend`.
# Shell env wins over .env files in Vite, so this empty default also
# neutralizes a stray frontend/.env LAN URL (.env.production is empty).
ARG VITE_API_URL=
ENV VITE_API_URL=$VITE_API_URL

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# Non-root: nginx listens on 8080 (no privileged bind needed). Frontend is
# same-origin only: VITE_API_URL is baked empty at build and /api/ + /v1/
# proxy to backend:8000 (see nginx.conf).

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

# Runtime state dirs must be writable for USER nginx (default nginx.conf
# uses /var/run/nginx.pid + cache/temp under /var/cache/nginx).
RUN touch /var/run/nginx.pid && \
    chown -R nginx:nginx /var/run/nginx.pid /var/cache/nginx /var/log/nginx /etc/nginx/conf.d /usr/share/nginx/html

USER nginx

EXPOSE 8080
