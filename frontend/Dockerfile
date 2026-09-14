FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# NOTE: stays root (default) because nginx listens on 80; USER nginx would
# break the bind. Frontend is same-origin only: VITE_API_URL is baked empty
# at build and /api/ + /v1/ proxy to backend:8000 (see nginx.conf).

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
