# DevOps handbook

Single source of truth for how the team's environment is deployed and operated. If you
take over DevOps, read this top to bottom once, then use the [handover checklist](#handover-checklist).

Applies to both repositories:

| Repo | Contents | Deploys |
|---|---|---|
| [`dmc-268-api-t3`](https://github.com/larchanka-training/dmc-268-api-t3) | backend (API + worker), **all infrastructure code** (`infra/`) | image `ghcr.io/larchanka-training/dmc-268-api-t3:<sha>` |
| [`dmc-268-ui-t3`](https://github.com/larchanka-training/dmc-268-ui-t3) | frontend (Vite + React) | image `ghcr.io/larchanka-training/dmc-268-ui-t3:<sha>` |

## 1. What runs where

One VPS (Contabo, Debian 13), address in the org variable `VPS_DMC268_IP_T3`
(`77.237.236.234`). There is a single environment: **production = `main`**. There is no
staging and no `develop` branch.

```
Internet :80/:443 ──► dmc268-caddy ──/api/*──► dmc268-api ──► dmc268-postgres
                                   └─ /*  ──► dmc268-web     dmc268-worker ──► dmc268-redis
```

| Container | Image | Role |
|---|---|---|
| `dmc268-caddy` | `caddy:2.10-alpine` | the **only** published ports (80, 443); HTTPS + routing |
| `dmc268-api` | backend image | FastAPI, `uvicorn --root-path /api` |
| `dmc268-worker` | backend image, command `python -m app.worker` | RQ worker (currently runs only the placeholder `ping` job; real review jobs come with issue #13) |
| `dmc268-migrate` | backend image, command `alembic upgrade head` | one-shot, re-created on every deploy |
| `dmc268-web` | frontend image (nginx-unprivileged, port 8080) | static files + SPA fallback |
| `dmc268-postgres` | `postgres:16-alpine` | volume `dmc268_postgres_data` |
| `dmc268-redis` | `redis:8-alpine` | volume `dmc268_redis_data`; queue |

Postgres, Redis, API, worker and web have **no** published ports; they talk over the
Docker network `dmc268`.

### URLs

- App: `https://77-237-236-234.sslip.io/` (see [HTTPS](#3-https)); plain `http://` redirects to it.
- API health: `/api/healthcheck`; API docs: `/api/docs`.

## 2. How a deploy works

Both repos have `.github/workflows/ci-cd.yml`:

1. **Every PR / push:** checks only (backend: pre-commit, pytest, Terraform fmt/validate,
   `docker compose` smoke incl. the worker; frontend: types, lint, format, build). PRs never deploy.
2. **Push to `main`, only if every check is green:** build the image, push it to GHCR
   tagged with the commit SHA, then deploy.
3. **Deploy job** (runs as the key-only SSH user `deploy`):
   1. skips itself if its commit is no longer the tip of `main` (prevents an older run
      rolling production back);
   2. `docker pull`s the image on the server (so Terraform finds it locally);
   3. uploads the files to a **staging directory** `/opt/dmc268/incoming/<repo>-<run>/`
      (backend: the whole `infra/` tree + `api.auto.tfvars.json` with secrets, mode 0600;
      frontend: only `web.auto.tfvars.json`);
   4. runs `infra/deploy.sh <staging dir>` on the server.
4. **`infra/deploy.sh`**, under `flock /opt/dmc268/deploy.lock` (so the two repos can never
   interleave): copies the staged files into `/opt/dmc268/infra`, runs Terraform
   (`terraform` in a container against the host Docker socket — `infra/apply.sh`) **migrations first**
   (`-target=docker_container.migrate`) and then everything else. If anything fails, the
   previous `*.auto.tfvars.json` files are restored, so a bad release can't poison later
   deploys from either repo.
5. **Smoke test:** HTTPS `/api/healthcheck`, the frontend, the HTTP→HTTPS redirect, and
   `python -m app.worker.smoke` inside the API container (enqueues a job and waits for the
   worker to complete it).

Key properties (each one was tested locally):

- A **failing migration** fails the deploy while the old API and worker keep running.
- Re-running a deploy is idempotent; the database volume survives (`prevent_destroy = true`).
- A frontend deploy before the first backend deploy fails fast with a clear message.
- During a backend deploy the API container is replaced; Caddy **holds requests for up to
  30 s** (`lb_try_duration`) instead of answering 502, so users see a short delay, not errors.
  (Measured locally under constant load: 10 × 502 without it, 0 with it.)

### Terraform

Lives in `infra/` of the backend repo. It runs **on the VPS** (not in CI), so its state
(`/opt/dmc268/infra/terraform.tfstate`) sits next to the containers it manages — there is no
remote state service. Back up that directory if you care about the state; the containers
can always be recreated by re-running a deploy.

Never run `terraform destroy` on the server: it would delete the Postgres volume
(`prevent_destroy` makes it refuse, on purpose).

Run Terraform locally against your own Docker (e.g. to test infra changes):

```bash
infra/apply.sh init
echo '{"api_image":"dmc268-api:local","postgres_password":"x","http_port":18080,"https_port":18443}' > infra/local.auto.tfvars.json
docker build -t dmc268-api:local .
infra/apply.sh apply
```

(`*.auto.tfvars.json` and state files are git-ignored. Clean up with `docker rm -f` of the
`dmc268-*` containers and volumes — `destroy` is blocked by design.)

### Rolling back

Revert the bad commit on `main` (`git revert <sha>` → PR → merge). The normal pipeline then
deploys the reverted code. Re-running an old workflow run does **not** work: its deploy job
sees it is no longer the tip of `main` and skips itself.

## 3. HTTPS

Caddy obtains and renews a **Let's Encrypt certificate automatically** — nothing to
operate. It needs a hostname, not a bare IP, so the pipeline uses the free wildcard DNS
service **sslip.io**: `77-237-236-234.sslip.io` resolves to `77.237.236.234`.

- The hostname is computed in the deploy jobs from `VPS_DMC268_IP_T3`, or taken from the
  **repo variable `SITE_ADDRESS`** if set (in *both* repos). It is passed to Terraform as
  `site_address` and to Caddy as `SITE_ADDRESS`.
- Certificates live in the Docker volume `dmc268_caddy_data`. **Do not delete it**: a new
  certificate is requested on every fresh start, and Let's Encrypt rate-limits that (5 per
  week per hostname).
- Ports 80 and 443 must stay open (80 is used for the certificate challenge and the redirect).
  The first deploy with a new hostname waits up to ~2.5 minutes for the certificate.
- If `site_address` is `:80` (the Terraform default) Caddy serves plain HTTP on any host.

### Switching to a real domain

1. Create a DNS **A record** `app.example.com → 77.237.236.234`.
2. In **both** repos add the repo variable `SITE_ADDRESS=app.example.com`
   (Settings → Secrets and variables → Actions → Variables).
3. Re-run the latest deploy of `main` in each repo (or merge anything). Caddy requests the
   certificate for the new name. The sslip.io name stops working after that; nothing else changes.

## 4. Secrets and configuration

All secrets are **GitHub Encrypted Secrets**; they reach the containers as environment
variables, written at deploy time into a mode-0600 file on the server
(`/opt/dmc268/infra/api.auto.tfvars.json`, inside a mode-0700 directory) and passed by
Terraform to the containers. They are never printed to logs or passed as command-line
arguments on the server.

| Name | Where | Used for |
|---|---|---|
| `VPS_DMC268_IP_T3` | org variable | server address |
| `VPS_DMC268_U` / `VPS_DMC268_P` | org secrets | **root** SSH login with a password. Used only by the *Bootstrap server* workflow, and as the break-glass way in (see §6) |
| `AI_DMC268_T3` | org secret | Eurorouter API key → `EUROROUTER_API_KEYS` |
| `AI_DMC268_URL` | org variable | → `EUROROUTER_BASE_URL` |
| `DEPLOY_SSH_KEY` | repo secret, **both repos** | private key of the `deploy` user, used by CI only |
| `POSTGRES_PASSWORD` | repo secret, backend | database password. Postgres reads it only when its volume is first created, so **do not rotate it** without also running `ALTER USER app PASSWORD '...'` inside `dmc268-postgres` |
| `SITE_ADDRESS` | optional repo variable, both repos | real domain instead of the sslip.io name |

Application settings are read from environment variables in `app/config.py`; the full list
is in the README. Database credentials are URL-escaped, so any password characters work.

Adding a new secret for the app: add it as a repo secret, add a Terraform variable
(`infra/variables.tf`, `sensitive = true`), put it into `local.app_env` in `infra/main.tf`,
and pass it in the "Stage config and secrets" step of `ci-cd.yml`.

## 5. Server layout

```
/opt/dmc268/                  owner deploy, 0750
  deploy.lock                 flock file (serialises deploys)
  incoming/<repo>-<run>/      per-run staging; removed after a successful deploy
  infra/                      live Terraform config, state, and secret tfvars (0700)
    terraform.tfstate         Terraform state — back this up
    api.auto.tfvars.json      backend settings and secrets (0600)
    web.auto.tfvars.json      current frontend image
```

Users: `root` (password login stays enabled on purpose, see §6) and `deploy` (key-only;
member of the `docker` group, which is root-equivalent — it exists only for CI and operators).

Useful commands (as `deploy`):

```bash
docker ps
docker logs --tail 200 dmc268-api        # also: dmc268-worker, dmc268-migrate, dmc268-caddy
docker exec dmc268-api python -m app.worker.smoke && echo worker-ok
docker exec dmc268-postgres psql -U app -d app
docker exec dmc268-redis redis-cli llen rq:queue:default   # queued jobs
```

Old GHCR images (older than 2 weeks) are pruned after each deploy; Docker Hub images stay cached.

## 6. Access and handover

There are three ways in, from most to least routine:

1. **Your own SSH key (normal).** Do *not* pass the CI private key around. Add your GitHub
   public SSH keys to the server with the workflow **Actions → "Add SSH access" →
   Run workflow**, entering GitHub usernames (e.g. `alice,bob`). The people need at least one
   public key in GitHub (Settings → SSH and GPG keys). Afterwards:
   `ssh deploy@77.237.236.234`. Every added key is tagged `github:<user>` in
   `~deploy/.ssh/authorized_keys`, so you can see who has access; remove a line there to revoke it.
2. **Root password (break-glass; intentionally kept enabled).** The team rotates DevOps every
   sprint, so the next person can always get in with the shared root credentials
   (`VPS_DMC268_U` / `VPS_DMC268_P`, ask the course admin or the previous DevOps for the
   values — GitHub never shows secret values): `ssh root@77.237.236.234`.
3. **Recovering CI access** (the `deploy` key is lost or `DEPLOY_SSH_KEY` is wrong): generate
   a new key pair (`ssh-keygen -t ed25519 -f key -N ''`), replace `infra/bootstrap/deploy_key.pub`
   with the new public key, set the new private key as the secret `DEPLOY_SSH_KEY` in both repos,
   and run **Actions → "Bootstrap server"**. It is idempotent: it logs in as root with the
   password, makes sure Docker and the `deploy` user exist, and appends the new key.

The original CI key pair was generated on the previous DevOps's laptop
(`~/.ssh/dmc268_t3_deploy`); the private half is in the `DEPLOY_SSH_KEY` secrets. It never
needs to be shared — use option 1 or 3.

### Handover checklist

- [ ] New DevOps added to the GitHub repos with admin rights (needed to manage secrets/variables).
- [ ] Their GitHub SSH key added with "Add SSH access"; they can `ssh deploy@77.237.236.234`.
- [ ] They have the root password (or know who does).
- [ ] They have read §2 and §3, and ran a deploy-neutral check: `docker ps` shows 6 containers.
- [ ] Old DevOps's key removed from `~deploy/.ssh/authorized_keys` if they should no longer have access.

## 7. Local development

See the README: `docker compose up` starts PostgreSQL, Redis, a one-shot migration, the API
and the worker. `uv run pre-commit install` installs the git hooks (ruff, ruff-format, mypy
strict, pylint, whitespace) — they auto-fix files on the first run, so re-stage and commit again.

## 8. Decisions and known limitations

| Decision | Why |
|---|---|
| **Redis + RQ** instead of RabbitMQ | the sprint tasks specify Redis; one container, no broker to operate. `SYSTEM_DESIGN.md` §6 was updated accordingly. |
| Terraform runs on the server, not in CI | state lives with the containers; no remote state service to run (other teams run a self-hosted S3 for this) |
| Docker Terraform provider, not Docker Compose | what the sprint task asks for (Terraform), and it gives plan/apply semantics for the containers |
| `sslip.io` for HTTPS | no domain available; switching to a real domain is a one-variable change (§3) |
| One image for API, worker and migrations | simplest; the command differs |
| Containers run as root | accepted for now (not fixed on purpose) |
| Root password SSH login kept enabled | the next DevOps needs a way in (§6) |

Known limitations:

- The worker only knows the placeholder `ping` job until issue #13 lands.
- `deploy` is in the `docker` group (root-equivalent) — acceptable for a course project.
- Terraform state is a single file on the server (no backup).
- Single server, no redundancy. A brief delay (≤ 30 s of held requests) occurs during backend deploys.
- The root password is shared; rotate it with the course admin if it leaks.

## 9. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Deploy fails at "Configure SSH"/`Permission denied` | `DEPLOY_SSH_KEY` doesn't match the server → §6.3 |
| `Host key verification failed` | the VPS was reinstalled: update `.github/ssh/known_hosts` in **both** repos (`ssh-keyscan 77.237.236.234`, verify the fingerprint with the provider panel) |
| Deploy fails, "alembic upgrade head failed" | a migration error; the old version is still running. The job prints `docker logs dmc268-migrate`. Fix the migration and merge again |
| `unauthorized` pulling the image | the GHCR package must allow the repo's `GITHUB_TOKEN` (package settings → "Manage Actions access"), or be public |
| HTTPS smoke test fails right after changing `SITE_ADDRESS` | certificate not issued yet (wait/retry), or the DNS record doesn't point at the server, or ports 80/443 are blocked at the provider |
| Site unreachable after a bad `SITE_ADDRESS` (HTTP redirects to an HTTPS name with no certificate) | set `SITE_ADDRESS` back to a working name (or the sslip.io one) and re-run the deploy; as a last resort set it to `:80` for plain HTTP |
| Frontend deploy says "Backend infra is not deployed" | merge/deploy the backend repo first |
| Port 5355 open in the post-deploy check of other teams' setups | not applicable here, but the bootstrap disables LLMNR/mDNS anyway |
| `terraform` says a resource "has lifecycle.prevent_destroy" | intended; the database volume can't be removed through Terraform |
