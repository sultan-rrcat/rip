#!/usr/bin/env python3
"""RIP compose lifecycle (single cross-platform entrypoint).

Replaces scripts/rip.sh (Linux/macOS) and scripts/rip.ps1 (Windows), which
were line-for-line mirrors. Run from anywhere in the repo::

    python scripts/rip.py <command> [services...] [flags]

Why this wrapper exists instead of raw ``docker compose``:

- ``up`` always builds: VITE_API_URL is baked at image build and the backend
  code layer is copied at build, so plain ``up`` silently reuses stale images.
- Stale shell DB_*/OLLAMA_* exports shadow .env for compose interpolation
  (POSTGRES_*, OLLAMA_BASE_URL), while the backend reads .env --
  cleared per invocation (in-process only, the parent shell is untouched).
- Host probes use 127.0.0.1, never bare localhost (IPv6-first hang).
- NOTE: .env PORT does NOT move the compose backend (docker-compose.yml
  pins container PORT=8000). Health URLs use HOST_*_PORT from .env.

Requires Python 3.11+ (stdlib only) and Docker with compose v2.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

STALE_ENV_KEYS = (
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DB_CONNECT_TIMEOUT_S",
    "OLLAMA_BASE_URL",
    "OLLAMA_DEFAULT_MODEL",
    "OLLAMA_TIMEOUT_MS",
    "OLLAMA_CONTEXT_WINDOW",
)

USAGE = """\
Usage: python scripts/rip.py <command> [services...] [flags]

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
"""


def die(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    sys.exit(code)


def dotenv_val(key: str, default: str = "") -> str:
    """Read KEY from .env (last occurrence wins, like docker compose)."""
    env_file = REPO_ROOT / ".env"
    value = default
    try:
        text = env_file.read_text(encoding="utf-8")
    except OSError:
        return value
    for raw_line in text.splitlines():
        line = raw_line.strip().rstrip("\r")
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, val = line.partition("=")
        if name.strip() != key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        value = val
    return value


def clear_stale_env() -> None:
    """Drop stale exports that would shadow .env for compose interpolation."""
    for key in STALE_ENV_KEYS:
        os.environ.pop(key, None)


def ensure_backend_cpus() -> None:
    """Dynamic default: host logical cores - 1 (min 2) for the ML backend.

    Explicit wins: shell export first, then .env value; only compute when
    neither is set. Compose falls back to 2.0 without the wrapper.
    """
    if (os.environ.get("BACKEND_CPUS") or "").strip():
        return
    if dotenv_val("BACKEND_CPUS", "").strip():
        return
    cores = os.cpu_count() or 0
    if cores <= 0:
        return
    quota = max(cores - 1, 2)
    os.environ["BACKEND_CPUS"] = str(quota)
    print(
        f"BACKEND_CPUS auto-set to {quota} "
        f"(host cores: {cores}; override via shell or .env)."
    )


def compose(*args: str) -> int:
    """Run `docker compose` with stale env cleared and CPU default ensured."""
    clear_stale_env()
    ensure_backend_cpus()
    proc = subprocess.run(["docker", "compose", *args], check=False)
    return proc.returncode


def compose_checked(*args: str) -> None:
    code = compose(*args)
    if code != 0:
        sys.exit(code)


def ensure_env_file() -> None:
    if not (REPO_ROOT / ".env").exists():
        example = REPO_ROOT / ".env.example"
        try:
            shutil.copyfile(example, REPO_ROOT / ".env")
        except OSError as exc:
            die(f"Could not create .env from .env.example: {exc}")
        print(
            "Created .env from .env.example -- edit OLLAMA_BASE_URL, "
            "BGE paths and HOST_PG_PORT, then run again."
        )
        sys.exit(1)


def http_get(url: str, timeout: float = 10.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as res:
            return 200 <= res.status < 400
    except Exception:  # noqa: BLE001 - probe returns False on any failure
        return False


def wait_backend_healthy(timeout_sec: int = 300) -> bool:
    port = dotenv_val("HOST_BACKEND_PORT", "8000")
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if http_get(url):
            return True
        time.sleep(10)
    return False


def show_urls() -> None:
    bport = dotenv_val("HOST_BACKEND_PORT", "8000")
    fport = dotenv_val("HOST_FRONTEND_PORT", "5173")
    print(f"frontend: http://127.0.0.1:{fport}")
    print(f"backend:  http://127.0.0.1:{bport}  (health: /api/health)")


def cmd_up(services: list[str]) -> None:
    ensure_env_file()
    compose_checked("up", "-d", "--build", *services)
    if wait_backend_healthy(300):
        print("Backend healthy.")
        compose_checked("ps")
        show_urls()
    else:
        die("Backend did not turn healthy in time -- run "
            "'python scripts/rip.py logs backend'.")


def cmd_fresh(args: list[str]) -> None:
    agreed = any(a in ("-y", "--yes") for a in args)
    services = [a for a in args if a not in ("-y", "--yes")]
    if not agreed:
        print("WARNING: this deletes pgdata AND uploads (DB, files, artifacts).")
        try:
            answer = input("Type YES to continue: ").strip()
        except EOFError:
            answer = ""
        if answer != "YES":
            print("Aborted.")
            return
    compose_checked("down", "-v")
    cmd_up(services)


def cmd_logs(args: list[str]) -> None:
    tail = "100"
    services: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--tail" and i + 1 < len(args):
            tail = args[i + 1]
            i += 2
        elif arg.startswith("--tail="):
            tail = arg.partition("=")[2]
            i += 1
        else:
            services.append(arg)
            i += 1
    compose_checked("logs", "-f", "--tail", tail, *services)


def cmd_migrate() -> None:
    db_user = dotenv_val("DB_USER", "rip")
    db_name = dotenv_val("DB_NAME", "rip")
    clear_stale_env()
    ready = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres",
         "pg_isready", "-U", db_user, "-d", db_name],
        capture_output=True,
        check=False,
    )
    if ready.returncode != 0:
        die("postgres is not running -- run 'python scripts/rip.py up' first.")
    schema = REPO_ROOT / "backend" / "schema.sql"
    try:
        with open(schema, "rb") as handle:
            proc = subprocess.run(
                ["docker", "compose", "exec", "-T", "postgres",
                 "psql", "-U", db_user, "-d", db_name,
                 "-v", "ON_ERROR_STOP=1", "-f", "-"],
                stdin=handle,
                check=False,
            )
    except OSError as exc:
        die(f"Could not read backend/schema.sql: {exc}")
    if proc.returncode != 0:
        sys.exit(proc.returncode)
    print(f"schema.sql re-applied to database '{db_name}'.")


def cmd_health() -> int:
    failed = False
    bport = dotenv_val("HOST_BACKEND_PORT", "8000")
    fport = dotenv_val("HOST_FRONTEND_PORT", "5173")
    if http_get(f"http://127.0.0.1:{bport}/api/health"):
        print(f"backend:  OK    http://127.0.0.1:{bport}/api/health")
    else:
        print(f"backend:  FAIL  http://127.0.0.1:{bport}/api/health")
        failed = True
    if http_get(f"http://127.0.0.1:{fport}/healthz"):
        print(f"frontend: OK    http://127.0.0.1:{fport}/healthz")
    else:
        print(f"frontend: FAIL  http://127.0.0.1:{fport}/healthz")
        failed = True
    db_user = dotenv_val("DB_USER", "rip")
    db_name = dotenv_val("DB_NAME", "rip")
    clear_stale_env()
    ready = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres",
         "pg_isready", "-U", db_user, "-d", db_name],
        capture_output=True,
        check=False,
    )
    if ready.returncode == 0:
        print("postgres: ready")
    else:
        print("postgres: FAIL (not running?)")
        failed = True
    show_urls()
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    os.chdir(REPO_ROOT)
    if not (REPO_ROOT / "docker-compose.yml").is_file():
        die(f"docker-compose.yml not found in {REPO_ROOT} -- run from the repo.")
    if shutil.which("docker") is None:
        die("docker CLI not found in PATH.")
    cmd = (argv[0] if argv else "help").lower()
    rest = argv[1:] if len(argv) > 1 else []
    if cmd == "up":
        cmd_up(rest)
    elif cmd == "down":
        compose_checked("down", *rest)
    elif cmd == "fresh":
        cmd_fresh(rest)
    elif cmd == "restart":
        compose_checked("restart", *rest)
    elif cmd == "rebuild":
        compose_checked("up", "-d", "--build", *rest)
        if wait_backend_healthy(300):
            print("Backend healthy.")
            show_urls()
        else:
            die("Backend did not turn healthy in time.")
    elif cmd == "logs":
        cmd_logs(rest)
    elif cmd in ("ps", "status"):
        compose_checked("ps", *rest)
    elif cmd == "migrate":
        cmd_migrate()
    elif cmd == "health":
        return cmd_health()
    else:
        print(USAGE)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
