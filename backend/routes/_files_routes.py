from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import config

router = APIRouter()
logger = config.setup_logging()

class FileCreate(BaseModel):
    file_name: str
    file_size: int

@router.get("/api/notebooks/{id}/files")
def get_files(id: str):
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT file_id, file_name, file_size, file_status, created_at
                FROM files
                WHERE notebook_id = %s
                ORDER BY created_at ASC
            """, (id,))
            rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "size": r[2], "status": r[3]} for r in rows]

@router.post("/api/notebooks/{id}/files")
def create_file(id: str, data: FileCreate):
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO files (notebook_id, file_name, file_size, file_status)
                VALUES (%s, %s, %s, 'processing')
                RETURNING file_id, file_name, file_size, file_status
            """, (id, data.file_name, data.file_size))
            r = cur.fetchone()
    return {"id": r[0], "name": r[1], "size": r[2], "status": r[3]}


@router.patch("/api/files/{file_id}/status")
def update_file_status(file_id: str, data: dict):
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE files SET file_status = %s WHERE file_id = %s
            """, (data["status"], file_id))
    return {"message": "updated"}


@router.delete("/api/files/{file_id}")
def delete_file(file_id: str):
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM files WHERE file_id = %s", (file_id,))
    return {"message": "deleted"}




# Run with: uvicorn main:router --host 0.0.0.0 --port 5000
