"""Create a new user in the RIP database.

Usage:
    python scripts/create_user.py <username> <password>

Or interactively:
    python scripts/create_user.py
"""

import sys
import os
import hashlib
import secrets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

import psycopg2


def create_user(username: str, password: str) -> None:
    salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    stored = f"{salt}${pw_hash}"

    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "rip"),
        user=os.getenv("DB_USER", "rip"),
        password=os.getenv("DB_PASSWORD", "rippass"),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, stored),
            )
        conn.commit()
        print(f"User '{username}' created successfully.")
    except psycopg2.IntegrityError:
        print(f"Error: Username '{username}' already exists.")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) == 3:
        create_user(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 1:
        username = input("Username: ").strip()
        password = input("Password: ").strip()
        if not username or not password:
            print("Username and password are required.")
            sys.exit(1)
        create_user(username, password)
    else:
        print(__doc__)
        sys.exit(1)
