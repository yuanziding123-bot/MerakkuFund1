# Deployment Assets

This directory contains deployment-only configuration. Runtime application code
stays under `polyagents/`; deployment wrappers stay here or at the repository
root when Docker tooling expects that convention.

- `nginx/aihf.agentlab.studio.conf` is the HTTP reverse-proxy config used by
  the server-side Nginx fallback.
- `../Dockerfile` and `../docker-compose.yml` remain at the repository root so
  plain `docker compose build` works without extra flags.
