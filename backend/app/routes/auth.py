from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg2 import errors as pg_errors
from pydantic import BaseModel

from app.core.config import settings
from app.core.db import pg_connection
from app.core.logging import setup_logging

router = APIRouter()
logger = setup_logging()

SESSION_COOKIE_NAME = "rip_session"
SESSION_DURATION_DAYS = 7

#: Access roles (ADR-052). Viewers keep every research power (notebooks,
#: files, messages, runs); only the admin panel (`/v1/admin/*` + `/admin`)
#: requires `admin`. Default is `viewer` — admin is explicitly granted.
VALID_ROLES = ("admin", "viewer")
DEFAULT_ROLE = "viewer"

# Rate limiting state (in-memory, per-IP)
_login_attempts: dict[str, list[float]] = defaultdict(list)
_lockouts: dict[str, float] = {}

# Rate limit constants
RATE_LIMIT_WINDOW_S = 60
RATE_LIMIT_MAX_ATTEMPTS = 5
LOCKOUT_DURATION_S = 900  # 15 minutes


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    user_id: str
    username: str
    role: str = DEFAULT_ROLE


def normalize_role(value: object) -> str:
    """Validate a role string; raise ValueError on anything outside VALID_ROLES."""
    text = str(value or "").strip().lower()
    if text not in VALID_ROLES:
        raise ValueError(f"role must be one of {list(VALID_ROLES)}")
    return text


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return dk.hex()


def _verify_password(password: str, salt: str, expected_hash: str) -> bool:
    return hmac.compare_digest(_hash_password(password, salt), expected_hash)


def _check_rate_limit(client_ip: str) -> None:
    """Enforce login rate limiting: 5 attempts/min/IP, 15-min lockout."""
    now = time.time()

    # Check if IP is locked out
    lockout_until = _lockouts.get(client_ip)
    if lockout_until and now < lockout_until:
        remaining = int(lockout_until - now)
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {remaining}s.",
            headers={"Retry-After": str(remaining)},
        )

    # Clean old lockout
    if lockout_until and now >= lockout_until:
        del _lockouts[client_ip]

    # Clean old attempts outside window
    _login_attempts[client_ip] = [
        t for t in _login_attempts[client_ip] if now - t < RATE_LIMIT_WINDOW_S
    ]

    # Check rate limit
    if len(_login_attempts[client_ip]) >= RATE_LIMIT_MAX_ATTEMPTS:
        _lockouts[client_ip] = now + LOCKOUT_DURATION_S
        raise HTTPException(
            status_code=429,
            detail=f"Too many login attempts. Locked out for {LOCKOUT_DURATION_S // 60} minutes.",
            headers={"Retry-After": str(LOCKOUT_DURATION_S)},
        )


def _record_failed_attempt(client_ip: str) -> None:
    """Record a failed login attempt."""
    _login_attempts[client_ip].append(time.time())


def create_user(username: str, password: str, role: str = DEFAULT_ROLE) -> None:
    role = normalize_role(role)
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(password, salt)
    stored = f"{salt}${pw_hash}"
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
            (username, stored, role),
        )


def ensure_role_column() -> None:
    """Idempotent startup migration for pre-role volumes (ADR-052).

    Same fail-soft contract as messages.ensure_artifacts_column: compose
    Postgres init runs schema.sql only on an empty pgdata volume, so a
    redeploy otherwise 500s on the role SELECT until someone re-applies
    DDL by hand. A failure here only warns — get_current_user also
    degrades to the legacy shape when the column is absent.
    """
    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute("ALTER TABLE public.users ADD COLUMN IF NOT EXISTS role text")
            cur.execute("UPDATE public.users SET role = 'viewer' WHERE role IS NULL")
            cur.execute("ALTER TABLE public.users ALTER COLUMN role SET DEFAULT 'viewer'")
    except Exception:
        logger.warning(
            "users.role ensure-column failed, continuing bare",
            exc_info=True,
        )


def get_current_user(request: Request) -> UserResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    with pg_connection() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """
                    SELECT u.user_id, u.username, COALESCE(u.role, 'viewer')
                    FROM sessions s
                    JOIN users u ON u.user_id = s.user_id
                    WHERE s.token = %s AND s.expires_at > %s
                    """,
                (token, datetime.now(UTC)),
            )
            row = cur.fetchone()
        except pg_errors.UndefinedColumn:
            # Pre-migration volume: roll back the aborted statement and
            # serve the legacy shape (viewer by default, never admin).
            conn.rollback()
            logger.warning("users.role column missing — serving legacy shape")
            cur.execute(
                """
                    SELECT u.user_id, u.username
                    FROM sessions s
                    JOIN users u ON u.user_id = s.user_id
                    WHERE s.token = %s AND s.expires_at > %s
                    """,
                (token, datetime.now(UTC)),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=401, detail="Session expired or invalid")
            return UserResponse(user_id=str(row[0]), username=row[1], role=DEFAULT_ROLE)

    if not row:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    return UserResponse(user_id=str(row[0]), username=row[1], role=str(row[2] or DEFAULT_ROLE))


def require_admin(user: UserResponse = Depends(get_current_user)) -> UserResponse:  # noqa: B008 - FastAPI Depends-in-default is canonical
    """Auth dependency for the admin panel (ADR-052): login + admin role.

    Non-admins get 403 (not 404 — the panel's existence is not secret,
    only its use is restricted).
    """
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.post("/api/auth/login")
def login(data: LoginRequest, request: Request, response: Response) -> UserResponse:
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    with pg_connection() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "SELECT user_id, password_hash, COALESCE(role, 'viewer') FROM users WHERE username = %s",
                (data.username,),
            )
        except pg_errors.UndefinedColumn:
            # Pre-migration volume: legacy shape, viewer by default.
            conn.rollback()
            cur.execute(
                "SELECT user_id, password_hash FROM users WHERE username = %s",
                (data.username,),
            )
            row = cur.fetchone()
            if not row:
                _record_failed_attempt(client_ip)
                raise HTTPException(status_code=401, detail="Invalid username or password")
            row = (row[0], row[1], DEFAULT_ROLE)
        else:
            row = cur.fetchone()

    if not row:
        _record_failed_attempt(client_ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    user_id, stored, role = row[0], row[1], (row[2] if len(row) > 2 else DEFAULT_ROLE)
    salt, expected_hash = stored.split("$", 1)

    if not _verify_password(data.password, salt, expected_hash):
        _record_failed_attempt(client_ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=SESSION_DURATION_DAYS)

    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, %s)",
            (token, user_id, expires_at),
        )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_DURATION_DAYS * 86400,
        secure=settings.env == "production",
    )

    return UserResponse(user_id=str(user_id), username=data.username, role=str(role or DEFAULT_ROLE))


@router.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE token = %s", (token,))

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=settings.env == "production",
    )
    return {"message": "logged out"}


@router.get("/api/auth/me")
def me(user: UserResponse = Depends(get_current_user)) -> UserResponse:  # noqa: B008 - FastAPI Depends-in-default is canonical
    return user
