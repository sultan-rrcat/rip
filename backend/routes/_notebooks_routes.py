from pydantic import BaseModel
from typing import Optional
from fastapi import APIRouter
from uuid import uuid4
import config


router = APIRouter()
logger = config.setup_logging()

class NotebookCreate(BaseModel):
    notebook_id: Optional[str] = None
    notebook_name: str

@router.get("/api/notebooks")
def get_notebooks():
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT notebook_id, notebook_name, created_at
                FROM notebooks 
                ORDER BY created_at DESC
                """
            )
            rows = cur.fetchall()

    return [
        {"notebook_id": result[0], "notebook_name": result[1], "created_at": result[2]}
        for result in rows
    ]

@router.post("/api/notebooks")
def create_notebooks(data: NotebookCreate):
    notebook_id = data.notebook_id or str(uuid4())
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO notebooks (notebook_id, notebook_name)
                VALUES (%s, %s)
                RETURNING notebook_id, notebook_name, created_at
                """,
                (notebook_id, data.notebook_name),
            )
            result = cur.fetchone()
    return {
        "notebook_id": result[0],
        "notebook_name": result[1],
        "created_at": result[2],
    }

@router.put("/api/notebooks/{id}")
def rename_notebook(id: str, data: dict):
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE notebooks
                SET notebook_name=%s
                WHERE notebook_id=%s
                """,
                (data["notebook_name"], id),
            )
    return {"message": "updated"}

@router.delete("/api/notebooks/{id}")
def delete_notebook(id: str):
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM notebooks WHERE notebook_id=%s",
                (id,)
            )
    return {"message": "deleted"}