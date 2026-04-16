from pydantic import BaseModel
from typing import Optional, Any
import config
from fastapi import APIRouter
import json

router = APIRouter()
logger = config.setup_logging()

class MessageCreate(BaseModel):
    role: str
    text: str
    sources: Optional[Any] = None

@router.get("/api/notebooks/{id}/messages")
def get_messages(id: str):
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT message_id, role, text, sources, created_at
                FROM messages
                WHERE notebook_id = %s
                ORDER BY created_at ASC
            """, (id,))
            rows = cur.fetchall()
    return [{"id": r[0], "role": r[1], "text": r[2], "sources":r[3], "created_at": r[4]} for r in rows]


@router.post("/api/notebooks/{id}/messages")
def create_message(id: str, data: MessageCreate):
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO messages (notebook_id, role, text, sources)
                VALUES (%s, %s, %s, %s)
                RETURNING message_id, role, text, sources, created_at
            """, (id, data.role, data.text, json.dumps(data.sources or [])))
            r = cur.fetchone()
    return {"id": r[0], "role": r[1], "text": r[2], "sources":r[3], "created_at": r[4]}
