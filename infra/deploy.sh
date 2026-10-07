#!/bin/sh
# Usage: deploy.sh <staging-dir>     (staging-dir = <app-dir>/incoming/<name>)
#
# Called over SSH by both repos' deploy jobs. The jobs only *stage* files; every
# change to the live config and the Terraform state happens here, under one
# lock, so the two repos can never interleave:
#   - backend stages the whole infra/ tree + api.auto.tfvars.json
#   - frontend stages only web.auto.tfvars.json
set -eu

if [ "${1:-}" != "--locked" ]; then
  self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  stage="$(cd "$1" && pwd)"
  exec flock "$(dirname "$(dirname "$stage")")/deploy.lock" "$self" --locked "$stage"
fi

stage="$2"
app="$(dirname "$(dirname "$stage")")"
live="$app/infra"
install -d -m 700 "$live"

# A failed deploy must not leave its settings behind: otherwise every later
# deploy, from either repo, would re-run the broken image.
backup="$(mktemp -d "$app/.backup.XXXXXX")"
trap 'rm -rf "$backup"' EXIT
for f in "$live"/*.auto.tfvars.json; do
  if [ -e "$f" ]; then cp -p "$f" "$backup"/; fi
done

restore_tfvars() {
  for f in "$stage"/*.auto.tfvars.json; do
    [ -e "$f" ] || continue
    name="$(basename "$f")"
    if [ -e "$backup/$name" ]; then cp -p "$backup/$name" "$live/$name"; else rm -f "$live/$name"; fi
  done
}

# Only a backend deploy ships *.tf; drop the old ones so deleted files go away.
if ls "$stage"/*.tf >/dev/null 2>&1; then
  find "$live" -maxdepth 1 -name '*.tf' -delete
fi
cp -pR "$stage"/. "$live"/

if [ ! -f "$live/main.tf" ] || [ ! -f "$live/api.auto.tfvars.json" ]; then
  echo "error: the backend stack has not been deployed yet; deploy dmc-268-api-t3 first." >&2
  restore_tfvars
  exit 1
fi

cd "$live"
# Migrations first, on their own: in a single apply Terraform stops the old API
# before it checks the migration result, so a failed migration would take the
# site down instead of just failing the deploy.
if ./apply.sh init -input=false -no-color \
  && ./apply.sh apply -input=false -auto-approve -no-color -target=docker_container.migrate \
  && ./apply.sh apply -input=false -auto-approve -no-color; then
  rm -rf "$stage"
else
  restore_tfvars
  exit 1
fi
