import os
import re
from typing import Any
from uuid import uuid4

import aiofiles
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from pydantic import BaseModel

from app.core.config import settings
from app.core.db import pg_connection
from app.core.dependencies import get_rag
from app.core.logging import setup_logging
from app.routes.auth import UserResponse, get_current_user
from app.services.file_processor import run_rag_pipeline

router = APIRouter()
logger = setup_logging()

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_CHUNK_SIZE = 8192


def _validate_uuid(value: str, field: str) -> None:
    if not _UUID_RE.match(value or ""):
        raise HTTPException(status_code=400, detail=f"Invalid {field}")


class FileCreate(BaseModel):
    file_name: str
    file_size: int


@router.get("/api/notebooks/{id}/files")
def get_files(id: str, user: UserResponse = Depends(get_current_user)):
    _validate_uuid(id, "notebook_id")
    logger.debug(f"Fetching files for notebook: {id}")

    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT f.file_id, f.file_name, f.file_size, f.file_status, f.created_at
                FROM files f
                JOIN notebooks n ON n.notebook_id = f.notebook_id
                WHERE f.notebook_id = %s AND n.owner_id = %s
                ORDER BY f.created_at ASC
            """,
                (id, user.user_id),
            )
            rows = cur.fetchall()

        logger.debug(f"Fetched {len(rows)} files for notebook: {id}")

        return [{"id": r[0], "name": r[1], "size": r[2], "status": r[3]} for r in rows]

    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error fetching files for notebook: {id}")
        raise HTTPException(status_code=500, detail="Failed to fetch files")


@router.post("/api/notebooks/{id}/files")
def create_file(id: str, data: FileCreate, user: UserResponse = Depends(get_current_user)):
    _validate_uuid(id, "notebook_id")
    logger.info(f"Creating file for notebook: {id}, name: {data.file_name}")

    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM notebooks WHERE notebook_id = %s AND owner_id = %s",
                (id, user.user_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Notebook not found")

            cur.execute(
                """
                INSERT INTO files (notebook_id, file_name, file_size, file_status)
                VALUES (%s, %s, %s, 'processing')
                RETURNING file_id, file_name, file_size, file_status
            """,
                (id, data.file_name, data.file_size),
            )
            r = cur.fetchone()

        logger.info(f"File created: {r[0]} for notebook: {id}")

        return {
            "id": r[0],
            "name": r[1],
            "size": r[2],
            "status": r[3],
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error creating file for notebook: {id}")
        raise HTTPException(status_code=500, detail="Failed to create file")


@router.patch("/api/files/{file_id}/status")
def update_file_status(file_id: str, data: dict, user: UserResponse = Depends(get_current_user)):
    _validate_uuid(file_id, "file_id")
    logger.info(f"Updating file status: {file_id}")

    try:
        status = data["status"]

        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE files f SET file_status = %s
                FROM notebooks n
                WHERE f.file_id = %s AND f.notebook_id = n.notebook_id AND n.owner_id = %s
            """,
                (status, file_id, user.user_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="File not found")

        logger.info(f"File status updated: {file_id} → {status}")
        return {"message": "updated"}

    except HTTPException:
        raise
    except KeyError:
        logger.warning("Missing 'status' in request body")
        raise HTTPException(status_code=400, detail="status is required")

    except Exception:
        logger.exception(f"Error updating file status: {file_id}")
        raise HTTPException(status_code=500, detail="Failed to update file status")


@router.delete("/api/files/{file_id}")
def delete_file(file_id: str, user: UserResponse = Depends(get_current_user)):
    _validate_uuid(file_id, "file_id")
    logger.info(f"Deleting file: {file_id}")

    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM files f
                USING notebooks n
                WHERE f.file_id = %s AND f.notebook_id = n.notebook_id AND n.owner_id = %s
            """,
                (file_id, user.user_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="File not found")

        logger.info(f"File deleted: {file_id}")
        return {"message": "deleted"}

    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error deleting file: {file_id}")
        raise HTTPException(status_code=500, detail="Failed to delete file")


@router.post("/api/files/upload")
async def upload(
    notebook_id: str = Form(...),
    file: UploadFile = File(...),
    user: UserResponse = Depends(get_current_user),
):
    _validate_uuid(notebook_id, "notebook_id")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in settings.allowed_extensions:
        raise HTTPException(
            status_code=415,
            detail=f"File type '{ext}' not allowed. Allowed: {settings.allowed_extensions}",
        )

    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM notebooks WHERE notebook_id = %s AND owner_id = %s",
                (notebook_id, user.user_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Notebook not found")
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Ownership check failed for notebook: {notebook_id}")
        raise HTTPException(status_code=500, detail="Failed to verify notebook ownership")

    file_id = str(uuid4())
    notebook_path = os.path.join(settings.upload_dir, notebook_id)
    os.makedirs(notebook_path, exist_ok=True)
    file_path = os.path.join(notebook_path, f"{file_id}{ext}")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    file_size = 0

    try:
        async with aiofiles.open(file_path, "wb") as f:
            while chunk := await file.read(_CHUNK_SIZE):
                file_size += len(chunk)
                if file_size > max_bytes:
                    await f.close()
                    os.remove(file_path)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds {settings.max_upload_size_mb} MB limit",
                    )
                await f.write(chunk)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Upload failed for notebook: {notebook_id}")
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail="Failed to save file")

    try:
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO files (file_id, notebook_id, file_name, file_size, file_status)
                VALUES (%s, %s, %s, %s, 'processing')
                RETURNING file_id, file_name, file_size, file_status
            """,
                (file_id, notebook_id, file.filename, file_size),
            )
            result = cur.fetchone()
    except Exception:
        logger.exception(f"DB insert failed for file: {file_id}")
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail="Failed to save file metadata")

    return {"id": result[0], "name": result[1], "size": result[2], "status": result[3]}


@router.post("/api/files/{file_id}/process")
def process_file(
    file_id: str,
    background_tasks: BackgroundTasks,
    rag: Any = Depends(get_rag),
    user: UserResponse = Depends(get_current_user),
):
    _validate_uuid(file_id, "file_id")
    if rag is None:
        raise HTTPException(
            status_code=503,
            detail="RAG models not loaded (BGE weights missing) — retry after restart",
        )
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM files f
            JOIN notebooks n ON n.notebook_id = f.notebook_id
            WHERE f.file_id = %s AND n.owner_id = %s
        """,
            (file_id, user.user_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="File not found")

    background_tasks.add_task(run_rag_pipeline, file_id, rag)
    return {"message": "processing started"}
