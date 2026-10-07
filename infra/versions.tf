terraform {
  required_version = ">= 1.16.0"

  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 4.6"
    }
  }
}

# Terraform runs on the VPS itself (see apply.sh), so it talks to the local
# Docker daemon and keeps its state next to this file.
provider "docker" {
  host = "unix:///var/run/docker.sock"
}
