from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form, Request, Depends
from pydantic import BaseModel
from typing import Optional
from core.logging import setup_logging
from core.db import pg_connection
from core.dependencies import get_rag
from services.file_processor import run_rag_pipeline
from rag.pipeline import RagPipeline
from uuid import uuid4
import os
import config

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


@router.post("/api/files/upload")
async def upload(notebook_id: str = Form(...), file: UploadFile = File(...)):
    file_id = str(uuid4())

    # Create dedicated notebook folder
    notebook_path = os.path.join(config.UPLOAD_DIR, notebook_id)
    os.makedirs(notebook_path, exist_ok=True)

    # Save file with file id
    file_path = os.path.join(notebook_path, f"{file_id}.pdf")

    content = await file.read()
    file_size = len(content)

    with open(file_path, "wb") as f:
        f.write(content)

    # Save metadata in DB
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO files (file_id, notebook_id, file_name, file_size, file_status)
                VALUES (%s, %s, %s, %s, 'processing')
                RETURNING file_id, file_name, file_size, file_status
                """,
                (file_id, notebook_id, file.filename, file_size),
            )
            result = cur.fetchone()
    return {"id": result[0], "name": result[1], "size": result[2], "status": result[3]}


@router.post("/api/files/{file_id}/process")
def process_file(file_id: str, background_tasks: BackgroundTasks, rag: RagPipeline = Depends(get_rag)):
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM files WHERE file_id=%s", (file_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="File not found")

    background_tasks.add_task(run_rag_pipeline, file_id, rag)
    return {"message": "processing started"}