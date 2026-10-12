#!/usr/bin/env bash
# Webhook smoke against the running compose stack with the smoke override. Needs a FRESH stack
# (`docker compose down -v` first): leftover rows or a stale volume can make the run misleading.
#   COMPOSE_FILE=docker-compose.yml:docker-compose.smoke.yml docker compose up -d --build --wait
#   scripts/smoke_webhooks.sh
# Same script in CI and locally. Simulated deliveries signed locally with the THROWAWAY test
# secrets read from the api container; this is not a live provider check. Prints no diff and
# no secret; on a mismatch it prints a short diagnostic and exits non-zero.
set -euo pipefail

cd "$(dirname "$0")/.."
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml:docker-compose.smoke.yml}"

URL="${SMOKE_API_URL:-http://localhost:8000}"
FIXTURES="tests/webhooks/fixtures"
HEAD_SHA="$(printf 'a%.0s' $(seq 1 40))"
BASE_SHA="$(printf 'b%.0s' $(seq 1 40))"
RUN="smoke-$(date +%s)-$$"
POLL_SECONDS="${SMOKE_POLL_SECONDS:-60}"

fail() {
  echo "SMOKE FAILED: $*" >&2
  exit 1
}

api_env() {
  docker compose exec -T api printenv "$1" | tr -d '\r\n' || fail "cannot read $1 from the api container (is the stack up?)"
}

psql_q() {
  docker compose exec -T postgres psql -U app -d app -tA -c "$1" | tr -d '\r' || fail "psql query failed (is postgres up?)"
}

# Secrets come from the compose override (single source of truth) into the environment of the
# replay script only; they are never passed as arguments.
GITHUB_WEBHOOK_SECRET="$(api_env GITHUB_WEBHOOK_SECRET)"
GITLAB_WEBHOOK_SIGNING_TOKEN="$(api_env GITLAB_WEBHOOK_SIGNING_TOKEN)"
GITLAB_WEBHOOK_SECRET_TOKEN="$(api_env GITLAB_WEBHOOK_SECRET_TOKEN)"
SMOKE_WRONG_SECRET="smoke-wrong-secret-not-real"
export GITHUB_WEBHOOK_SECRET GITLAB_WEBHOOK_SIGNING_TOKEN GITLAB_WEBHOOK_SECRET_TOKEN SMOKE_WRONG_SECRET

replay() {
  local out
  out="$(python3 scripts/replay_webhook.py "$@" 2>&1)" \
    || fail "replay_webhook.py $1 (case: ${CASE}): $out"
}

row_count() { # provider delivery_id
  psql_q "select count(*) from vcs_deliveries where provider='$1' and delivery_id='$2'"
}

wait_succeeded() { # provider delivery_id
  local i status
  for ((i = 0; i < POLL_SECONDS; i++)); do
    status="$(psql_q "select lower(status) from vcs_deliveries where provider='$1' and delivery_id='$2'")"
    [ "$status" = "succeeded" ] && return 0
    sleep 1
  done
  echo "delivery $2 status='${status:-}' last_error='$(psql_q "select left(coalesce(last_error,''),300) from vcs_deliveries where provider='$1' and delivery_id='$2'")'" >&2
  fail "delivery $2 did not reach succeeded within ${POLL_SECONDS}s"
}

context_check() { # repository external id, MR number, expected head, expected base
  local row
  row="$(psql_q "select c.head_sha, c.base_sha, length(c.diff_text) > 0, c.schema_version is not null,
      jsonb_array_length(c.structured_context->'files') > 0
    from context_payloads c
    join review_jobs j on j.id = c.review_job_id
    join merge_requests m on m.id = j.merge_request_id
    join repositories r on r.id = m.repository_id
    where r.external_id = '$1' and m.external_id = '$2'")"
  [ "$row" = "$3|$4|t|t|t" ] || fail "context for $1#$2 unexpected (rows: $(printf '%s\n' "$row" | grep -c .), got '${row//[^|tf]/}')"
  [ "$(psql_q "select count(*) from vcs_deliveries where repository_external_id = '${1#*:}' and pr_number = $2 and provider = '${1%%:*}'")" = "1" ] \
    || fail "expected exactly one delivery row for $1#$2"
}

echo "[1] GitHub pull_request opened (signed) -> accepted -> succeeded with context"
CASE=1
GH_ID="$RUN-gh"
replay github --url "$URL/webhooks/github" --payload "$FIXTURES/github_pull_request_opened.json" \
  --delivery-id "$GH_ID" --expect accepted
wait_succeeded github "$GH_ID"
context_check "github:octo/repo" 42 "$HEAD_SHA" "$BASE_SHA"

echo "[2] redelivery and same revision -> duplicate, still one row"
CASE=2
replay github --url "$URL/webhooks/github" --payload "$FIXTURES/github_pull_request_opened.json" \
  --delivery-id "$GH_ID" --expect duplicate
replay github --url "$URL/webhooks/github" --payload "$FIXTURES/github_pull_request_opened.json" \
  --delivery-id "$RUN-gh-again" --expect duplicate
context_check "github:octo/repo" 42 "$HEAD_SHA" "$BASE_SHA"

echo "[3] GitLab merge request open (signed) -> accepted -> succeeded with context"
CASE=3
GL_ID="$RUN-gl"
replay gitlab --url "$URL/webhooks/gitlab" --payload "$FIXTURES/gitlab_merge_request_open.json" \
  --delivery-id "$GL_ID" --expect accepted
wait_succeeded gitlab "$GL_ID"
context_check "gitlab:123" 7 "$HEAD_SHA" "$BASE_SHA"

echo "[4] negative authentication -> 401 and zero rows"
CASE=4
expect_401() { # provider delivery_id args...
  local provider="$1" id="$2"
  shift 2
  replay "$provider" --url "$URL/webhooks/$provider" --delivery-id "$id" --expect-status 401 "$@"
  [ "$(row_count "$provider" "$id")" = "0" ] || fail "rejected delivery $id left a row"
}
expect_401 github "$RUN-gh-unsigned" --payload "$FIXTURES/github_pull_request_synchronize.json" --unsigned
expect_401 github "$RUN-gh-wrong" --payload "$FIXTURES/github_pull_request_synchronize.json" \
  --secret-env SMOKE_WRONG_SECRET
expect_401 gitlab "$RUN-gl-old" --payload "$FIXTURES/gitlab_merge_request_update_without_oldrev.json" \
  --timestamp-offset -600
expect_401 gitlab "$RUN-gl-token" --payload "$FIXTURES/gitlab_merge_request_update_without_oldrev.json" \
  --gitlab-mode token

echo "[5] GitHub ping (signed) -> ignored, no row"
CASE=5
replay github --url "$URL/webhooks/github" --payload "$FIXTURES/github_ping.json" \
  --event ping --delivery-id "$RUN-gh-ping" --expect ignored
[ "$(row_count github "$RUN-gh-ping")" = "0" ] || fail "ping left a row"

echo "SMOKE OK: webhook -> inbox -> worker -> context verified (simulated deliveries)"
