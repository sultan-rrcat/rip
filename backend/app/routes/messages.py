from pydantic import BaseModel
from typing import Optional, Any
from fastapi import APIRouter, HTTPException
import json
from app.core.logging import setup_logging
from app.core.db import pg_connection

router = APIRouter()
logger = setup_logging()

class MessageCreate(BaseModel):
    role: str
    text: str
    sources: Optional[Any] = None


@router.get("/api/notebooks/{id}/messages")
def get_messages(id: str):
    logger.info(f"Fetching messages for notebook: {id}")

    try:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT message_id, role, text, sources, created_at
                    FROM messages
                    WHERE notebook_id = %s
                    ORDER BY created_at ASC
                """, (id,))
                rows = cur.fetchall()

        logger.info(f"Fetched {len(rows)} messages for notebook: {id}")

        return [
            {
                "id": r[0],
                "role": r[1],
                "text": r[2],
                "sources": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]

    except Exception:
        logger.exception(f"Error fetching messages for notebook: {id}")
        raise HTTPException(status_code=500, detail="Failed to fetch messages")


@router.post("/api/notebooks/{id}/messages")
def create_message(id: str, data: MessageCreate):
    logger.info(f"Creating message for notebook: {id}, role: {data.role}")

    try:
        with pg_connection() as conn:
            with conn.cursor() as cur:
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

        logger.info(f"Message created: {r[0]} for notebook: {id}")

        return {
            "id": r[0],
            "role": r[1],
            "text": r[2],
            "sources": r[3],
            "created_at": r[4],
        }

    except Exception:
        logger.exception(f"Error creating message for notebook: {id}")
        raise HTTPException(status_code=500, detail="Failed to create message")