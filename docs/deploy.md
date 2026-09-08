# Deploy on a cheap VPS

**Google Cloud (trial credits):** see [deploy-gcp.md](deploy-gcp.md).

One host runs API, worker, MIT (OCR/inpaint), and Caddy (HTTPS).
Translation still calls OpenRouter. Sign-in is Google/Apple OAuth (cookie sessions).
`users.plan` / `subscription_status` are stubs for later paid plans (incl. crypto).

## Requirements

- VPS: preferably **≥8 GB RAM**, ≥40 GB disk, x86_64
  - Good start for SaaS MVP: **Hetzner Cloud CX32** (~8 GB) or **CX42** (~16 GB) if you expect parallel jobs
  - Stay on CPU for now; add a GPU box only when queue latency hurts
- Domain: `A` (and optionally `AAAA`) record → VPS IP
- Docker Engine + Docker Compose plugin
- OpenRouter API key

No GPU required. First extract downloads MIT models into the `mit-models` volume.

## Build on your PC or on the VPS?

| | On VPS (`docker compose build`) | On your Mac |
|--|--|--|
| Pros | Native `linux/amd64`, no transfer of multi‑GB image | Faster iteration if you already have Docker |
| Cons | First build is slow on a small VPS | Apple Silicon images **won’t run** on a typical x86 VPS unless you `docker buildx --platform linux/amd64` (slow under QEMU) |

**Recommendation:** build on the VPS (or in CI for `linux/amd64`). Building on a Mac only helps if you push an amd64 image to a registry and pull it on the server.

## 1. Server prep

```bash
# Ubuntu/Debian example
sudo apt update
sudo apt install -y git ca-certificates curl
# Install Docker: https://docs.docker.com/engine/install/
```

Open ports **80** and **443**. Do not publish port 8000 publicly.

## 2. Clone and configure

```bash
git clone https://github.com/ValDagon/ai-comic-translator.git
cd ai-comic-translator
cp .env.example .env
```

Edit `.env`:

1. `DOMAIN` — your hostname  
2. `OPENROUTER_API_KEY`  
3. `COMIC_SESSION_SECRET` — long random string  
4. Leave `COMIC_ALLOW_REGISTER=1` while the team (under 10 people) signs up; later set `0` and use invites/paid signup  

Compose sets `COMIC_SESSION_HTTPS_ONLY=1` for both the API and worker services (Secure cookies behind Caddy; `COMIC_APP_ENV=release` refuses to start without it on either service).

## 3. Start

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f worker
```

Build is large (PyTorch CPU + MIT deps). First boot may take many minutes.

UI: `https://YOUR_DOMAIN/` → **Register** / **Login**, then use the app.

## 4. Smoke checklist

1. Open `https://YOUR_DOMAIN/` → public landing. Use **Log in** / OAuth → `/app`.  
2. Create a project, upload **1–2** pages.  
3. Start job `full`. Worker logs show MIT; first run may download models.  
4. When `done`, download ZIP and check balloons.  
5. Log out; confirm `/projects` API returns 401 without a session.  
6. Confirm port **8000** is not public.  
7. Confirm `COMIC_SESSION_SECRET` is set (≥32 chars) and `COMIC_APP_ENV=release`.  
8. Retention: set up daily cron for `scripts/purge_stale_projects.py` (see **Project retention** below).

```bash
docker compose exec worker printenv MIT_REPO
docker compose exec worker ls /app/manga-image-translator
```

## 5. Ops

| Task | Command / note |
|------|----------------|
| Logs | `docker compose logs -f api worker caddy` |
| Restart | `docker compose restart` |
| Update app | `git pull && docker compose up -d --build` |
| Backup | snapshot volume `comic-data` (SQLite + `data/projects/`) |
| Models | volume `mit-models` — keep across rebuilds |
| Close registration | `COMIC_ALLOW_REGISTER=0` then `docker compose up -d api` |
| Retention / purge | See **Project retention** below; default `COMIC_PROJECT_RETENTION_DAYS=3` |

The container runs as a non-root user (`appuser`, uid 1000). The entrypoint
(`scripts/docker-entrypoint.sh`) starts as root, fixes ownership of
`/app/data` and `/app/manga-image-translator/models` if they aren't already
owned by uid 1000 — including volumes written by an older root-user image —
then drops to `appuser` before running the app. This is self-healing on every
container start, so upgrading from an older image needs no manual step
beyond the normal `docker compose up -d --build`.

If you're stuck on an image from **before** this entrypoint existed (i.e.
`sqlite3.OperationalError: attempt to write a readonly database` after
upgrading straight to a non-root image without it), fix ownership once by
hand, then pull/rebuild to get the self-healing entrypoint:

```bash
docker compose run --rm --user root api \
  chown -R 1000:1000 /app/data /app/manga-image-translator/models
git pull && docker compose up -d --build
```

Backup example:

```bash
docker run --rm -v ai-comic-translator_comic-data:/data -v "$PWD:/backup" alpine \
  tar czf /backup/comic-data-$(date +%F).tar.gz -C /data .
```

## Project retention (cron)

Uploaded pages and translations are **not kept forever**. Projects older than
`COMIC_PROJECT_RETENTION_DAYS` (default **3**) are removed from SQLite and
`data/projects/` by `scripts/purge_stale_projects.py`. Projects with a queued or
running job are skipped.

**On the VPS (recommended):** run daily from cron on the host (adjust path):

```bash
# crontab -e
15 4 * * * cd /path/to/ai-comic-translator && docker compose exec -T worker python scripts/purge_stale_projects.py >> /var/log/comic-purge.log 2>&1
```

Or without Docker, from the app directory with the same env as the worker:

```bash
COMIC_DATA_DIR=/app/data COMIC_DATABASE=/app/data/app.db python scripts/purge_stale_projects.py
```

Set `COMIC_PROJECT_RETENTION_DAYS` in `.env` (Compose passes it to the worker).
Manual dry run:

```bash
docker compose exec worker python scripts/purge_stale_projects.py
```

## Auth now / billing later

- **Now:** Google/Apple OAuth only (`COMIC_APP_ENV=release` in Compose), per-user projects (`projects.user_id`), session cookie. Set `COMIC_GOOGLE_*` / `COMIC_APPLE_*` (and optional `COMIC_SMTP_*` for account-deletion emails). Locally use `COMIC_APP_ENV=dev` (no OAuth). Migrating from password auth wipes old users/projects in SQLite.  
- **Later:** enforce `plan` / `subscription_status` (and crypto checkout) before create-job/upload; keep the same `users` row.  
- Do not put shared Caddy basic_auth in front anymore — it fights real accounts.

## Optional: pin MIT commit

```bash
# in .env
MIT_COMMIT=95227a2bb0fd306cd4f0c104d57284026f991b3a
docker compose build --no-cache api worker
docker compose up -d
```
