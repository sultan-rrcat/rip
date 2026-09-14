import os
from contextlib import contextmanager

import psycopg2
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))


def _db_params() -> dict:
    """Resolve DB params from env with Settings fallback; normalize host."""
    from app.core.config import _normalize_db_host, settings

    host = os.getenv("DB_HOST", settings.db_host)
    port = os.getenv("DB_PORT", str(settings.db_port))
    name = os.getenv("DB_NAME", settings.db_name)
    user = os.getenv("DB_USER", settings.db_user)
    password = os.getenv("DB_PASSWORD", settings.db_password)
    try:
        timeout = int(os.getenv("DB_CONNECT_TIMEOUT_S", str(settings.db_connect_timeout_s)))
    except ValueError:
        timeout = settings.db_connect_timeout_s
    return {
        "host": _normalize_db_host(host or ""),
        "port": port,
        "dbname": name,
        "user": user,
        "password": password,
        "connect_timeout": timeout,
    }

@contextmanager
def pg_connection():
    params = _db_params()
    conn = None
    try:
        conn = psycopg2.connect(**params)
        yield conn
        conn.commit()  # ✅ commit if everything went fine

    except Exception as e:
        if conn:
            conn.rollback()  # ✅ rollback on failure
        print(f"Error connecting to database: {e}")
        print(
            "DB Credentials -> "
            f"Host: {params['host']}, "
            f"Port: {params['port']}, "
            f"Name: {params['dbname']}, "
            f"User: {params['user']}, "
            "Password: ***"
        )
        raise

    finally:
        if conn:
            conn.close()  # ✅ always close connection