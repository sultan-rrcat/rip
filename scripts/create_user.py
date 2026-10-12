"""Create a new user in the RIP database (ADR-052 roles).

Usage:
    python scripts/create_user.py <username> <password> [--role admin|viewer]
    python scripts/create_user.py --set-role <username> <admin|viewer>

Or interactively:
    python scripts/create_user.py

Roles: viewer (default, full research powers, no admin panel) or admin.
The FIRST user in an empty users table always becomes admin (bootstrap
cannot lock itself out) — the requested role is noted but overridden.
"""

import hashlib
import os
import secrets
import sys

# Must stay in sync with backend/app/routes/auth.py VALID_ROLES.
VALID_ROLES = ("admin", "viewer")
DEFAULT_ROLE = "viewer"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

import psycopg2


def _connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "rip"),
        user=os.getenv("DB_USER", "rip"),
        password=os.getenv("DB_PASSWORD", "rippass"),
    )


def normalize_role(value: str) -> str:
    text = (value or "").strip().lower()
    if text not in VALID_ROLES:
        print(f"Error: role must be one of {list(VALID_ROLES)}.")
        sys.exit(1)
    return text


def create_user(username: str, password: str, role: str = DEFAULT_ROLE) -> None:
    role = normalize_role(role)
    salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    stored = f"{salt}${pw_hash}"

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            empty = (cur.fetchone() or [0])[0] == 0
            if empty and role != "admin":
                print("Note: first user in the database — granting admin (bootstrap).")
                role = "admin"
            try:
                cur.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                    (username, stored, role),
                )
            except psycopg2.errors.UndefinedColumn:
                # Pre-migration volume: legacy shape (role backfilled at boot).
                conn.rollback()
                cur.execute(
                    "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                    (username, stored),
                )
        conn.commit()
        print(f"User '{username}' created successfully (role: {role}).")
    except psycopg2.IntegrityError:
        print(f"Error: Username '{username}' already exists.")
        sys.exit(1)
    finally:
        conn.close()


def set_role(username: str, role: str) -> None:
    role = normalize_role(role)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET role = %s WHERE username = %s", (role, username))
            if cur.rowcount == 0:
                print(f"Error: Username '{username}' not found.")
                sys.exit(1)
        conn.commit()
        print(f"User '{username}' role set to '{role}'.")
    finally:
        conn.close()


def _parse_role_flag(args: list) -> tuple[list, str]:
    role = DEFAULT_ROLE
    rest = []
    i = 0
    while i < len(args):
        if args[i] == "--role" and i + 1 < len(args):
            role = args[i + 1]
            i += 2
        else:
            rest.append(args[i])
            i += 1
    return rest, role


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv[:1] == ["--set-role"]:
        if len(argv) != 3:
            print(__doc__)
            sys.exit(1)
        set_role(argv[1], argv[2])
    elif len(argv) >= 2:
        rest, role = _parse_role_flag(argv)
        if len(rest) != 2:
            print(__doc__)
            sys.exit(1)
        create_user(rest[0], rest[1], role)
    elif len(argv) == 0:
        username = input("Username: ").strip()
        password = input("Password: ").strip()
        raw_role = input(f"Role {list(VALID_ROLES)} [{DEFAULT_ROLE}]: ").strip() or DEFAULT_ROLE
        if not username or not password:
            print("Username and password are required.")
            sys.exit(1)
        create_user(username, password, raw_role)
    else:
        print(__doc__)
        sys.exit(1)
