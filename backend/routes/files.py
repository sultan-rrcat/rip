from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from core.logging import setup_logging
from core.db import pg_connection

router = APIRouter()
logger = setup_logging()

class FileCreate(BaseModel):
    file_name: str
    file_size: int


@router.get("/api/notebooks/{id}/files")
def get_files(id: str):
    logger.info(f"Fetching files for notebook: {id}")

    try:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT file_id, file_name, file_size, file_status, created_at
                    FROM files
                    WHERE notebook_id = %s
                    ORDER BY created_at ASC
                """, (id,))
                rows = cur.fetchall()

        logger.info(f"Fetched {len(rows)} files for notebook: {id}")

        return [
            {"id": r[0], "name": r[1], "size": r[2], "status": r[3]}
            for r in rows
        ]

    except Exception:
        logger.exception(f"Error fetching files for notebook: {id}")
        raise HTTPException(status_code=500, detail="Failed to fetch files")


@router.post("/api/notebooks/{id}/files")
def create_file(id: str, data: FileCreate):
    logger.info(f"Creating file for notebook: {id}, name: {data.file_name}")

    try:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO files (notebook_id, file_name, file_size, file_status)
                    VALUES (%s, %s, %s, 'processing')
                    RETURNING file_id, file_name, file_size, file_status
                """, (id, data.file_name, data.file_size))
                r = cur.fetchone()

        logger.info(f"File created: {r[0]} for notebook: {id}")

        return {
            "id": r[0],
            "name": r[1],
            "size": r[2],
            "status": r[3],
        }

    except Exception:
        logger.exception(f"Error creating file for notebook: {id}")
        raise HTTPException(status_code=500, detail="Failed to create file")


@router.patch("/api/files/{file_id}/status")
def update_file_status(file_id: str, data: dict):
    logger.info(f"Updating file status: {file_id}")

    try:
        status = data["status"]

        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE files SET file_status = %s WHERE file_id = %s
                """, (status, file_id))

        logger.info(f"File status updated: {file_id} → {status}")
        return {"message": "updated"}

    except KeyError:
        logger.warning("Missing 'status' in request body")
        raise HTTPException(status_code=400, detail="status is required")

    except Exception:
        logger.exception(f"Error updating file status: {file_id}")
        raise HTTPException(status_code=500, detail="Failed to update file status")


@router.delete("/api/files/{file_id}")
def delete_file(file_id: str):
    logger.info(f"Deleting file: {file_id}")

    try:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM files WHERE file_id = %s",
                    (file_id,)
                )

        logger.info(f"File deleted: {file_id}")
        return {"message": "deleted"}

    except Exception:
        logger.exception(f"Error deleting file: {file_id}")
        raise HTTPException(status_code=500, detail="Failed to delete file")