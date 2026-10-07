variable "api_image" {
  description = "Backend image (API, worker and migrations), e.g. ghcr.io/larchanka-training/dmc-268-api-t3:<sha>."
  type        = string
}

variable "web_image" {
  description = "Frontend static image, written by the dmc-268-ui-t3 deploy. The placeholder serves until the first UI deploy."
  type        = string
  default     = "nginxinc/nginx-unprivileged:1.30-alpine"
}

variable "postgres_password" {
  type      = string
  sensitive = true
}

variable "eurorouter_api_keys" {
  description = "Comma-separated Eurorouter API keys."
  type        = string
  sensitive   = true
  default     = ""
}

variable "eurorouter_base_url" {
  type    = string
  default = ""
}

variable "eurorouter_model" {
  type    = string
  default = "gpt-4o-mini"
}

variable "http_port" {
  description = "Host port the public entry (Caddy) binds to."
  type        = number
  default     = 80
}
