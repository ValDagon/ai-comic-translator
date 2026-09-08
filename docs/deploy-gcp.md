# Deploy on Google Cloud (free trial / credits)

Same stack as [deploy.md](deploy.md): one **Compute Engine** VM runs **Docker Compose** (API, worker, Caddy).
Translation uses OpenRouter; sign-in is Google/Apple OAuth.

Trial credits (~$300 / 90 days) are enough for an **e2-standard-2** (8 GB RAM) or **e2-standard-4** (16 GB) if you set a **budget alert** and delete the VM when done.

## What you need before starting

| Item | Notes |
|------|--------|
| GCP account + billing | Required even for trial credits |
| Domain | `A` record → VM **static external IP** |
| GitHub | Public clone URL, or deploy key for a private repo |
| Secrets in `.env` | See [.env.example](../.env.example) |

Recommended VM for MIT on CPU + dozens of comics/month (with [3-day retention](../scripts/purge_stale_projects.py)):

- **Machine type:** `e2-standard-2` (2 vCPU, 8 GB) — start here; bump to `e2-standard-4` if worker OOMs  
- **Boot disk:** Ubuntu 22.04 LTS, **80–100 GB** balanced persistent disk  
- **Region:** closest to users (e.g. `europe-west3`)  
- **Firewall:** allow **TCP 80, 443** from the internet; do **not** expose **8000**

## 1. Create project and budget

1. [Google Cloud Console](https://console.cloud.google.com/) → create a project (e.g. `comic-translator`).  
2. **Billing** → link account (trial credits apply here).  
3. **Billing → Budgets & alerts** → e.g. alert at **$50** and **$150** so you do not burn credits silently.

Enable **Compute Engine API** (Console will prompt on first VM create).

## 2. Reserve a static IP

**VPC network → IP addresses → Reserve external static IP** (regional, same region as the VM).

Point your domain:

```text
comics.example.com.  A  <STATIC_IP>
```

Wait for DNS before first HTTPS request (Let’s Encrypt via Caddy).

## 3. Create the VM (Console)

**Compute Engine → VM instances → Create instance**

| Field | Value |
|-------|--------|
| Name | `comic-translator` |
| Machine type | `e2-standard-2` |
| Boot disk | Ubuntu 22.04 LTS, 80+ GB |
| External IP | the static IP from step 2 |
| Firewall | tick **Allow HTTP traffic** and **Allow HTTPS traffic** |

**Advanced options → Automation → Startup script** — paste contents of [scripts/gcp/metadata-startup.sh](../scripts/gcp/metadata-startup.sh), or install Docker manually ([Docker docs](https://docs.docker.com/engine/install/ubuntu/)).

Create the instance. First boot installs Docker (1–2 minutes).

### Same thing with `gcloud` (optional)

```bash
export PROJECT_ID=comic-translator
export ZONE=europe-west3-a
export STATIC_IP=YOUR_RESERVED_IP

gcloud config set project "$PROJECT_ID"

gcloud compute instances create comic-translator \
  --zone="$ZONE" \
  --machine-type=e2-standard-2 \
  --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud \
  --boot-disk-size=100GB \
  --address="$STATIC_IP" \
  --tags=http-server,https-server \
  --metadata-from-file=startup-script=scripts/gcp/metadata-startup.sh

gcloud compute firewall-rules create allow-http-https \
  --allow=tcp:80,tcp:443 \
  --target-tags=http-server,https-server \
  --direction=INGRESS \
  --priority=1000
```

## 4. SSH and clone from GitHub

Console → VM → **SSH**, or:

```bash
gcloud compute ssh comic-translator --zone="$ZONE"
```

On the VM:

```bash
sudo mkdir -p /opt/ai-comic-translator
sudo chown "$USER:$USER" /opt/ai-comic-translator
git clone https://github.com/ValDagon/ai-comic-translator.git /opt/ai-comic-translator
cd /opt/ai-comic-translator
cp .env.example .env
nano .env   # DOMAIN, OPENROUTER_API_KEY, COMIC_SESSION_SECRET, OAuth, etc.
```

Generate session secret:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Google OAuth (same GCP org is fine)

[APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials) → **OAuth client ID** (Web):

- **Authorized JavaScript origins:** `https://YOUR_DOMAIN`  
- **Authorized redirect URIs:** `https://YOUR_DOMAIN/auth/google/callback` (confirm path in [web/auth_routes.py](../web/auth_routes.py) if you change routes)

Put **Client ID** and **Client secret** into `.env` as `COMIC_GOOGLE_CLIENT_ID` / `COMIC_GOOGLE_CLIENT_SECRET`.

## 5. Build and run

On the VM (build on the server — native `linux/amd64`):

```bash
cd /opt/ai-comic-translator
docker compose up -d --build
docker compose ps
docker compose logs -f worker
```

First `docker compose build` is **large** (PyTorch + MIT). First **full** job downloads models into volume `mit-models`.

Open `https://YOUR_DOMAIN/` → login → upload 1–2 pages → job `full`.

Smoke checklist: same as [deploy.md §4](deploy.md#4-smoke-checklist).

## 6. Retention: delete projects older than 3 days

In `.env`:

```bash
COMIC_PROJECT_RETENTION_DAYS=3
```

Compose passes this into the **api** container (default `3` if unset).

Daily cron on the VM:

```bash
crontab -e
```

```cron
15 4 * * * cd /opt/ai-comic-translator && /usr/bin/docker compose exec -T api python scripts/purge_stale_projects.py >> /var/log/comic-purge.log 2>&1
```

Tell users to download ZIP within 3 days.

## 7. Updates from GitHub

```bash
cd /opt/ai-comic-translator
git pull
docker compose up -d --build
```

Optional later: **Cloud Build** → **Artifact Registry** → VM pulls image instead of building on the VM (faster updates, slightly more setup).

## 8. Ops on GCP

| Task | How |
|------|-----|
| Logs | `docker compose logs -f api worker caddy` |
| Stop to save money | `docker compose down` or stop VM in Console (disk still billed) |
| Backup SQLite + small data | Snapshot boot disk or tar `comic-data` volume ([deploy.md](deploy.md)) |
| Delete everything | Delete VM + release static IP + delete disk if not needed |

**Cost tips:** stopping the VM stops vCPU/RAM charges; **persistent disk** and **static IP** (while reserved) still cost. Delete the instance and disk when the trial ends if you migrate to Hetzner.

## 9. What not to use (for this repo)

- **GKE** — overkill for one MVP host.

Stick to **one GCE VM + Docker Compose** for the full pipeline (worker + disk). Cloud Run is API-only unless you add a worker elsewhere.

## Cloud Run (GitHub → Build → Run)

Cloud Run по умолчанию запускает **только** `CMD` образа — **uvicorn** (API). Сервис **`worker`** из `docker-compose.yml` на Run **не создаётся**: нет второго контейнера, нет общего volume между двумя сервисами, worker **не слушает HTTP** (Run требует порт для health check).

Job'ы остаются в SQLite со статусом **`queued`**, пока не крутится `python worker.py`.

### Вариант A — нормальный прод (рекомендуется)

**Compute Engine VM + `docker compose up`** — отдельные контейнеры `api`, `worker`, `caddy`, общие volumes `comic-data` и `mit-models`. См. шаги 1–5 выше в этом файле.

### Вариант B — worker в том же контейнере Cloud Run (эксперимент)

Один контейнер: worker в фоне + API на `$PORT`.

1. Задеплойте образ, где есть [`scripts/cloudrun-entrypoint.sh`](../scripts/cloudrun-entrypoint.sh).
2. Cloud Run → **Edit** → **Container**:
   - **Container command:** `/app/scripts/cloudrun-entrypoint.sh`
   - **Container arguments:** пусто
   - **Container port:** `8080` или `8000` (как у uvicorn)
3. **Min instances:** `1` (иначе при scale-to-zero worker исчезает).
4. **Memory:** лучше **8 GiB** (MIT extract на CPU).
5. **Request timeout:** до **3600 s** для длинных job'ов (worker в фоне, но инстанс должен жить).
6. Те же env, что для API (`OPENROUTER_API_KEY`, `COMIC_APP_ENV=release`, …).

Логи worker: Logs → строки `[job …]` / `Worker:`.

**Минусы Run:** диск эфемерный (новый инстанс — пустая `comic-data` и модели качаются снова); один инстанс — одна тяжёлая job; для десятков комиксов всё равно лучше VM.

| Setting | Value |
|---------|--------|
| Container port | `8000` (or uvicorn on `$PORT` / `8080`) |
| `COMIC_APP_ENV` | `release` |
| `COMIC_SESSION_HTTPS_ONLY` | `1` (app trusts `X-Forwarded-Proto` on Run) |
| `COMIC_PROXY_TRUSTED_HOSTS` | `*` (Run's edge proxy isn't on a private IP; the app now defaults to trusting only private-network peers, so Cloud Run needs this explicitly — the container port isn't reachable except through Run's proxy anyway) |
| Google redirect URI | `https://YOUR-SERVICE.run.app/auth/google/callback` |

OAuth `redirect_uri` is taken from **`X-Forwarded-Host`** on Cloud Run (must match Google Console). After deploy, check Logs for `google oauth start redirect_uri=…` and add that exact URI in Google Credentials if missing.

| Login error | Likely cause |
|-------------|----------------|
| Сессия входа устарела | cookies / `COMIC_SESSION_SECRET` changed mid-flow |
| Не удалось войти | token exchange — wrong client secret or redirect URI mismatch in Google Console |
| Email не подтверждён | use a verified Gmail |

Redeploy a new revision after any env change; test login in a private window.

## Troubleshooting (VM)

| Symptom | Check |
|---------|--------|
| HTTPS fails | DNS → static IP; ports 80/443 open; `docker compose logs caddy` |
| OAuth redirect mismatch | Redirect URI exactly matches Google console |
| Worker killed (OOM) | Resize to `e2-standard-4`; watch `dmesg` / `docker compose logs worker` |
| Build runs out of disk | `docker system df`; enlarge disk or `docker builder prune` |

See also [deploy.md](deploy.md) for MIT pin, registration lock (`COMIC_ALLOW_REGISTER=0`), and volume names.
