# syntax=docker/dockerfile:1
FROM node:22-alpine

# This simulator has ZERO npm dependencies: every line of the OAuth 2.1
# protocol is implemented on Node core modules. So there is no npm install,
# no lockfile to drift, and the build works with no network access.
WORKDIR /app
COPY shared/ ./shared/
COPY authorization-server/ ./authorization-server/

ENV NODE_ENV=production
RUN addgroup -S app && adduser -S app -G app && chown -R app:app /app
USER app

EXPOSE 9000
CMD ["node", "authorization-server/server.js"]
