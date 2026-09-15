#!/usr/bin/env bash
# RIP compose lifecycle (Linux/macOS entrypoint; mirror: scripts/rip.ps1).
#
# Why this wrapper exists instead of raw `docker compose`:
# - `up` always builds: VITE_API_URL is baked at image build and the backend
#   code layer is copied at build, so plain `up` silently reuses stale images.
# - Stale shell DB_* exports shadow .env for compose interpolation
#   (POSTGRES_*), while the backend reads .env — cleared per invocation.
# - Host probes use 127.0.0.1, never bare localhost (IPv6-first hang).
# - NOTE: .env PORT does NOT move the compose backend (docker-compose.yml
#   pins container PORT=8000). Health URLs use HOST_*_PORT from .env.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f "$REPO_ROOT/docker-compose.yml" ]]; then
  echo "docker-compose.yml not found in $REPO_ROOT — run from the repo." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker CLI not found in PATH." >&2
  exit 1
fi

# Read a KEY from .env (last occurrence wins, like docker compose); empty
# when missing so callers apply their default. CRLF-tolerant.
dotenv_val() {
  local key="$1" default="${2:-}" val=""
  if [[ -f "$REPO_ROOT/.env" ]]; then
    val="$(grep -E "^${key}=" "$REPO_ROOT/.env" | tail -n 1 | cut -d= -f2- | tr -d '\r' || true)"
  fi
  echo "${val:-$default}"
}

clear_stale_db_env() {
  # Session-only: stale exports shadow .env for compose interpolation.
  unset DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD DB_CONNECT_TIMEOUT_S || true
}

compose() {
  clear_stale_db_env
  docker compose "$@"
}

ensure_env_file() {
  if [[ ! -f "$REPO_ROOT/.env" ]]; then
    cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
    echo "Created .env from .env.example — edit OLLAMA_BASE_URL, BGE paths and HOST_PG_PORT, then run again."
    exit 1
  fi
}

http_get() {
  local url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -o /dev/null --max-time 10 "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O /dev/null -T 10 "$url"
  else
    python3 -c "import sys,urllib.request; urllib.request.urlopen(sys.argv[1], timeout=10)" "$url"
  fi
}

wait_backend_healthy() {
  local timeout_sec="${1:-300}" port url start
  port="$(dotenv_val HOST_BACKEND_PORT 8000)"
  url="http://127.0.0.1:${port}/api/health"
  start="$(date +%s)"
  while (( $(date +%s) - start < timeout_sec )); do
    if http_get "$url" 2>/dev/null; then
      return 0
    fi
    sleep 10
  done
  return 1
}

show_urls() {
  local bport fport
  bport="$(dotenv_val HOST_BACKEND_PORT 8000)"
  fport="$(dotenv_val HOST_FRONTEND_PORT 5173)"
  echo "frontend: http://127.0.0.1:${fport}"
  echo "backend:  http://127.0.0.1:${bport}  (health: /api/health)"
}

usage() {
  cat <<'EOF'
Usage: scripts/rip.sh <command> [services...] [flags]

Commands:
  up [services]       Env guard + `up -d --build` + wait healthy + URLs
  down                Stop and remove containers (volumes kept)
  fresh [-y|--yes]    WIPE pgdata + uploads (`down -v`), then up --build
  restart [service]   Bounce without rebuild (volumes kept)
  rebuild [service]   `up -d --build` scoped (default: all)
  logs [service]      Follow logs (`--tail N` supported)
  ps | status          Compose service table
  migrate             Re-apply backend/schema.sql to running postgres
  health              Probe backend /api/health, frontend /healthz, postgres
  help                This text
EOF
}

cmd_up() {
  ensure_env_file
  compose up -d --build "$@"
  if wait_backend_healthy 300; then
    echo "Backend healthy."
    compose ps
    show_urls
  else
    echo "Backend did not turn healthy in time — run 'scripts/rip.sh logs backend'." >&2
    exit 1
  fi
}

cmd_fresh() {
  local yes="" services=() a
  for a in "$@"; do
    case "$a" in
      -y|--yes) yes="1" ;;
      *) services+=("$a") ;;
    esac
  done
  if [[ -z "$yes" ]]; then
    echo "WARNING: this deletes pgdata AND uploads (DB, files, artifacts)."
    read -r -p "Type YES to continue: " answer
    if [[ "$answer" != "YES" ]]; then
      echo "Aborted."
      exit 0
    fi
  fi
  compose down -v
  cmd_up "${services[@]}"
}

cmd_logs() {
  local tail="100" services=()
  while (($# > 0)); do
    case "$1" in
      --tail) tail="$2"; shift 2 ;;
      --tail=*) tail="${1#--tail=}"; shift ;;
      *) services+=("$1"); shift ;;
    esac
  done
  compose logs -f --tail "$tail" "${services[@]}"
}

cmd_migrate() {
  local db_user db_name
  db_user="$(dotenv_val DB_USER rip)"
  db_name="$(dotenv_val DB_NAME rip)"
  clear_stale_db_env
  if ! docker compose exec -T postgres pg_isready -U "$db_user" -d "$db_name" >/dev/null 2>&1; then
    echo "postgres is not running — run 'scripts/rip.sh up' first." >&2
    exit 1
  fi
  docker compose exec -T postgres psql -U "$db_user" -d "$db_name" -v ON_ERROR_STOP=1 -f - < "$REPO_ROOT/backend/schema.sql"
  echo "schema.sql re-applied to database '$db_name'."
}

cmd_health() {
  local failed=0 bport fport db_user db_name
  bport="$(dotenv_val HOST_BACKEND_PORT 8000)"
  fport="$(dotenv_val HOST_FRONTEND_PORT 5173)"
  if http_get "http://127.0.0.1:${bport}/api/health" 2>/dev/null; then
    echo "backend:  OK    http://127.0.0.1:${bport}/api/health"
  else
    echo "backend:  FAIL  http://127.0.0.1:${bport}/api/health"
    failed=1
  fi
  if http_get "http://127.0.0.1:${fport}/healthz" 2>/dev/null; then
    echo "frontend: OK    http://127.0.0.1:${fport}/healthz"
  else
    echo "frontend: FAIL  http://127.0.0.1:${fport}/healthz"
    failed=1
  fi
  db_user="$(dotenv_val DB_USER rip)"
  db_name="$(dotenv_val DB_NAME rip)"
  clear_stale_db_env
  if docker compose exec -T postgres pg_isready -U "$db_user" -d "$db_name" >/dev/null 2>&1; then
    echo "postgres: ready"
  else
    echo "postgres: FAIL (not running?)"
    failed=1
  fi
  show_urls
  return "$failed"
}

cmd="${1:-help}"
shift || true
# tr-based lowercase (bash 3.2-safe for macOS /bin/bash).
cmd="$(printf '%s' "$cmd" | tr '[:upper:]' '[:lower:]')"
case "$cmd" in
  up) cmd_up "$@" ;;
  down) compose down "$@" ;;
  fresh) cmd_fresh "$@" ;;
  restart) compose restart "$@" ;;
  rebuild)
    if (($# == 0)); then
      compose up -d --build
    else
      compose up -d --build "$@"
    fi
    if wait_backend_healthy 300; then
      echo "Backend healthy."
      show_urls
    else
      echo "Backend did not turn healthy in time." >&2
      exit 1
    fi
    ;;
  logs) cmd_logs "$@" ;;
  ps|status) compose ps "$@" ;;
  migrate) cmd_migrate ;;
  health) cmd_health ;;
  *) usage ;;
esac
