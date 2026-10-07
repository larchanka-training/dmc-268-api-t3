#!/bin/sh
# Runs Terraform in a container against this host's Docker daemon. Runs as the
# invoking user (plus the docker socket's group) so state files stay owned by
# that user rather than root.
set -eu
cd "$(dirname "$0")"
exec docker run --rm \
  --user "$(id -u):$(id -g)" \
  --group-add "$(stat -c %g /var/run/docker.sock 2>/dev/null || echo 0)" \
  -e HOME=/tmp \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD":/workspace \
  -w /workspace \
  hashicorp/terraform:1.16.5 "$@"
