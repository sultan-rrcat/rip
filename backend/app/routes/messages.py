from pydantic import BaseModel
from typing import Optional, Any, Literal
from fastapi import APIRouter, Depends, HTTPException
import json
from psycopg2 import errors as pg_errors
from app.core.logging import setup_logging
from app.core.db import pg_connection
from app.routes.auth import UserResponse, get_current_user

router = APIRouter()
logger = setup_logging()

MessageRole = Literal["user", "assistant", "error"]

class MessageCreate(BaseModel):
    role: MessageRole
    text: str
    sources: Optional[Any] = None
    artifacts: Optional[Any] = None


_ARTIFACTS_DDL = "ALTER TABLE public.messages ADD COLUMN IF NOT EXISTS artifacts jsonb"


def ensure_artifacts_column() -> None:
    """Idempotent startup migration for pre-existing volumes.

    Compose Postgres init runs schema.sql only on an empty pgdata volume,
    so a redeploy with new DDL otherwise 500s until someone re-applies it
    by hand. Fail-soft by design: a failure here only warns — the routes
    below also degrade to the legacy shape when the column is absent, so
    boot never crashes on DB trouble.
    """
    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(_ARTIFACTS_DDL)
    except Exception:
        logger.warning(
            "messages.artifacts ensure-column failed, continuing bare",
            exc_info=True,
        )


@router.get("/api/notebooks/{id}/messages")
def get_messages(id: str, user: UserResponse = Depends(get_current_user)):
    logger.info(f"Fetching messages for notebook: {id}")

    try:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("""
                        SELECT m.message_id, m.role, m.text, m.sources,
                               COALESCE(m.artifacts, '[]'::jsonb), m.created_at
                        FROM messages m
                        JOIN notebooks n ON n.notebook_id = m.notebook_id
                        WHERE m.notebook_id = %s AND n.owner_id = %s
                        ORDER BY m.created_at ASC
                    """, (id, user.user_id))
                except pg_errors.UndefinedColumn:
                    # Pre-migration volume: roll back the aborted statement
                    # and serve the legacy shape (no stored artifacts).
                    conn.rollback()
                    logger.warning(
                        f"messages.artifacts column missing for notebook {id} "
                        "— serving legacy shape"
                    )
                    cur.execute("""
                        SELECT m.message_id, m.role, m.text, m.sources, m.created_at
                        FROM messages m
                        JOIN notebooks n ON n.notebook_id = m.notebook_id
                        WHERE m.notebook_id = %s AND n.owner_id = %s
                        ORDER BY m.created_at ASC
                    """, (id, user.user_id))
                    rows = cur.fetchall()
                    return [
                        {
                            "id": r[0],
                            "role": r[1],
                            "text": r[2],
                            "sources": r[3],
                            "artifacts": [],
                            "created_at": r[4],
                        }
                        for r in rows
                    ]
                rows = cur.fetchall()

        logger.info(f"Fetched {len(rows)} messages for notebook: {id}")

        return [
            {
                "id": r[0],
                "role": r[1],
                "text": r[2],
                "sources": r[3],
                "artifacts": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    except Exception:
        logger.exception(f"Error fetching messages for notebook: {id}")
        raise HTTPException(status_code=500, detail="Failed to fetch messages")


@router.post("/api/notebooks/{id}/messages")
def create_message(id: str, data: MessageCreate, user: UserResponse = Depends(get_current_user)):
    logger.info(f"Creating message for notebook: {id}, role: {data.role}")

    try:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM notebooks WHERE notebook_id = %s AND owner_id = %s",
                    (id, user.user_id),
                )
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Notebook not found")

                try:
                    cur.execute("""
                        INSERT INTO messages
                            (notebook_id, role, text, sources, artifacts)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING message_id, role, text, sources,
                                  COALESCE(artifacts, '[]'::jsonb), created_at
                    """, (
                        id,
                        data.role,
                        data.text,
                        json.dumps(data.sources or []),
                        json.dumps(data.artifacts or [])
                    ))
                except pg_errors.UndefinedColumn:
                    # Pre-migration volume: drop the artifacts payload rather
                    # than fail the write (chart refs are lost, chat survives).
                    conn.rollback()
                    logger.warning(
                        f"messages.artifacts column missing for notebook {id} "
                        "— storing without artifacts"
                    )
                    cur.execute("""
                        INSERT INTO messages (notebook_id, role, text, sources)
                        VALUES (%s, %s, %s, %s)
                        RETURNING message_id, role, text, sources, created_at
                    """, (
                        id,
                        data.role,
                        data.text,
                        json.dumps(data.sources or [])
                    ))
                    r = cur.fetchone()
                    return {
                        "id": r[0],
                        "role": r[1],
                        "text": r[2],
                        "sources": r[3],
                        "artifacts": [],
                        "created_at": r[4],
                    }
                r = cur.fetchone()

        logger.info(f"Message created: {r[0]} for notebook: {id}")

        return {
            "id": r[0],
            "role": r[1],
            "text": r[2],
            "sources": r[3],
            "artifacts": r[4],
            "created_at": r[5],
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error creating message for notebook: {id}")
        raise HTTPException(status_code=500, detail="Failed to create message")