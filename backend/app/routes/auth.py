from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.db import pg_connection
from app.core.logging import setup_logging

router = APIRouter()
logger = setup_logging()

SESSION_COOKIE_NAME = "rip_session"
SESSION_DURATION_DAYS = 7


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    user_id: str
    username: str


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return dk.hex()


def _verify_password(password: str, salt: str, expected_hash: str) -> bool:
    return hmac.compare_digest(_hash_password(password, salt), expected_hash)


def create_user(username: str, password: str) -> None:
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(password, salt)
    stored = f"{salt}${pw_hash}"
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, stored),
            )


def get_current_user(request: Request) -> UserResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.user_id, u.username
                FROM sessions s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.token = %s AND s.expires_at > %s
                """,
                (token, datetime.now(timezone.utc)),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    return UserResponse(user_id=str(row[0]), username=row[1])


@router.post("/api/auth/login")
def login(data: LoginRequest, response: Response) -> UserResponse:
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, password_hash FROM users WHERE username = %s",
                (data.username,),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    user_id, stored = row
    salt, expected_hash = stored.split("$", 1)

    if not _verify_password(data.password, salt, expected_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DURATION_DAYS)

    with pg_connection() as conn:
        with conn.cursor() as cur:
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
    )

    return UserResponse(user_id=str(user_id), username=data.username)


@router.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE token = %s", (token,))

    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return {"message": "logged out"}


@router.get("/api/auth/me")
def me(user: UserResponse = Depends(get_current_user)) -> UserResponse:
    return user
