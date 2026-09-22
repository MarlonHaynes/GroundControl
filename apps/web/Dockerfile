# syntax=docker/dockerfile:1
# Development image — the compose stack bind-mounts the source for hot reload.
FROM node:24-alpine

WORKDIR /app

# Dependency layer, cached independently of source changes.
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]
