locals {
  prefix = "dmc268"

  app_env = [
    "POSTGRES_DB=app",
    "POSTGRES_USER=app",
    "POSTGRES_PASSWORD=${var.postgres_password}",
    "POSTGRES_HOST=postgres",
    "POSTGRES_PORT=5432",
    "REDIS_URL=redis://redis:6379/0",
    "EUROROUTER_BASE_URL=${var.eurorouter_base_url}",
    "EUROROUTER_API_KEYS=${var.eurorouter_api_keys}",
    "EUROROUTER_MODEL=${var.eurorouter_model}",
  ]
}

resource "docker_network" "app" {
  name = local.prefix
}

resource "docker_volume" "postgres_data" {
  name = "${local.prefix}_postgres_data"

  # Guard rail: this holds the database. A config mistake or an accidental
  # `destroy` must fail instead of deleting it.
  lifecycle {
    prevent_destroy = true
  }
}

resource "docker_volume" "redis_data" {
  name = "${local.prefix}_redis_data"
}

resource "docker_volume" "caddy_data" {
  name = "${local.prefix}_caddy_data"
}

resource "docker_image" "postgres" {
  name         = "postgres:16-alpine"
  keep_locally = true
}

resource "docker_image" "redis" {
  name         = "redis:8-alpine"
  keep_locally = true
}

resource "docker_image" "caddy" {
  name         = "caddy:2.10-alpine"
  keep_locally = true
}

# App images are pulled by the deploy workflows (GHCR needs auth), so these
# find them locally instead of pulling. keep_locally leaves old tags for rollback.
resource "docker_image" "api" {
  name         = var.api_image
  keep_locally = true
}

resource "docker_image" "web" {
  name         = var.web_image
  keep_locally = true
}

resource "docker_container" "postgres" {
  name    = "${local.prefix}-postgres"
  image   = docker_image.postgres.image_id
  restart = "unless-stopped"
  env = [
    "POSTGRES_DB=app",
    "POSTGRES_USER=app",
    "POSTGRES_PASSWORD=${var.postgres_password}",
  ]

  networks_advanced {
    name    = docker_network.app.id
    aliases = ["postgres"]
  }

  volumes {
    volume_name    = docker_volume.postgres_data.name
    container_path = "/var/lib/postgresql/data"
  }

  healthcheck {
    test     = ["CMD-SHELL", "pg_isready -U app -d app"]
    interval = "5s"
    timeout  = "5s"
    retries  = 10
  }

  wait         = true
  wait_timeout = 120
}

resource "docker_container" "redis" {
  name    = "${local.prefix}-redis"
  image   = docker_image.redis.image_id
  restart = "unless-stopped"

  networks_advanced {
    name    = docker_network.app.id
    aliases = ["redis"]
  }

  volumes {
    volume_name    = docker_volume.redis_data.name
    container_path = "/data"
  }

  healthcheck {
    test     = ["CMD", "redis-cli", "ping"]
    interval = "5s"
    timeout  = "5s"
    retries  = 10
  }

  wait         = true
  wait_timeout = 60
}

# Forces the migrate container to be re-created on every apply, so
# `alembic upgrade head` runs on each deploy (a no-op when the schema is current).
resource "terraform_data" "every_apply" {
  triggers_replace = timestamp()
}

resource "docker_container" "migrate" {
  name     = "${local.prefix}-migrate"
  image    = docker_image.api.image_id
  command  = ["alembic", "upgrade", "head"]
  env      = local.app_env
  must_run = false
  attach   = true
  logs     = true

  networks_advanced {
    name = docker_network.app.id
  }

  depends_on = [docker_container.postgres]

  lifecycle {
    replace_triggered_by = [terraform_data.every_apply]

    # api and worker depend on this resource, so a failed migration stops the
    # apply before new code starts against the old schema.
    postcondition {
      condition     = self.exit_code == 0
      error_message = "alembic upgrade head failed; run `docker logs dmc268-migrate` on the server (the CI deploy job prints them)."
    }
  }
}

resource "docker_container" "api" {
  name    = "${local.prefix}-api"
  image   = docker_image.api.image_id
  restart = "unless-stopped"
  # Caddy strips /api before proxying; --root-path makes /api/docs resolve.
  command = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--root-path", "/api"]
  env     = local.app_env

  networks_advanced {
    name    = docker_network.app.id
    aliases = ["api"]
  }

  healthcheck {
    test     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthcheck')"]
    interval = "5s"
    timeout  = "5s"
    retries  = 10
  }

  wait         = true
  wait_timeout = 120

  depends_on = [docker_container.migrate, docker_container.redis]
}

resource "docker_container" "worker" {
  name    = "${local.prefix}-worker"
  image   = docker_image.api.image_id
  restart = "unless-stopped"
  command = ["python", "-m", "app.worker"]
  env     = local.app_env

  networks_advanced {
    name = docker_network.app.id
  }

  depends_on = [docker_container.migrate, docker_container.redis]
}

resource "docker_container" "web" {
  name    = "${local.prefix}-web"
  image   = docker_image.web.image_id
  restart = "unless-stopped"

  networks_advanced {
    name    = docker_network.app.id
    aliases = ["web"]
  }
}

# The only container with a published port: plain HTTP on the server IP
# (no domain, so no TLS certificate).
resource "docker_container" "caddy" {
  name    = "${local.prefix}-caddy"
  image   = docker_image.caddy.image_id
  restart = "unless-stopped"

  ports {
    internal = 80
    external = var.http_port
  }

  upload {
    content = file("${path.module}/Caddyfile")
    file    = "/etc/caddy/Caddyfile"
  }

  networks_advanced {
    name = docker_network.app.id
  }

  volumes {
    volume_name    = docker_volume.caddy_data.name
    container_path = "/data"
  }

  depends_on = [docker_container.api, docker_container.web]
}
