#!/bin/sh
# Server-side deploy entrypoint, called over SSH by both repos' deploy jobs.
# flock serialises them: they apply the same Terraform state.
set -eu
cd "$(dirname "$0")"
exec flock ../deploy.lock sh -euc '
  ./apply.sh init -input=false -no-color
  # Migrations first, on their own: in a single apply Terraform stops the old
  # API before it checks the migration result, so a failed migration would
  # take the site down instead of just failing the deploy.
  ./apply.sh apply -input=false -auto-approve -no-color -target=docker_container.migrate
  ./apply.sh apply -input=false -auto-approve -no-color
'
