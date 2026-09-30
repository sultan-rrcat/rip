from pydantic import BaseModel
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from uuid import uuid4
from app.core.logging import setup_logging
from app.core.db import pg_connection
from app.routes.auth import UserResponse, get_current_user

router = APIRouter()
logger = setup_logging()

class NotebookCreate(BaseModel):
    notebook_id: Optional[str] = None
    notebook_name: str

@router.get("/api/notebooks")
def get_notebooks(user: UserResponse = Depends(get_current_user)):
    logger.info(f"Fetching notebooks for user: {user.username}")
    try:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT notebook_id, notebook_name, created_at
                    FROM notebooks
                    WHERE owner_id = %s
                    ORDER BY created_at DESC
                """,
                    (user.user_id,),
                )
                rows = cur.fetchall()

        logger.info(f"Fetched {len(rows)} notebooks")

        return [
            {"notebook_id": result[0], "notebook_name": result[1], "created_at": result[2]}
            for result in rows
        ]
    except Exception as e:
        logger.exception("Error fetching notebooks")
        raise HTTPException(status_code=500, detail="Failed to fetch notebooks")


@router.post("/api/notebooks")
def create_notebooks(data: NotebookCreate, user: UserResponse = Depends(get_current_user)):
    notebook_id = data.notebook_id or str(uuid4())
    logger.info(f"Creating notebook: {notebook_id} for user: {user.username}")

    try:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO notebooks (notebook_id, notebook_name, owner_id)
                    VALUES (%s, %s, %s)
                    RETURNING notebook_id, notebook_name, created_at
                """,
                    (notebook_id, data.notebook_name, user.user_id),
                )
                result = cur.fetchone()

        logger.info(f"Notebook created: {notebook_id}")

        return {
            "notebook_id": result[0],
            "notebook_name": result[1],
            "created_at": result[2],
        }
    except Exception as e:
        logger.exception("Error creating notebook")
        raise HTTPException(status_code=500, detail="Failed to create notebook")


@router.put("/api/notebooks/{id}")
def rename_notebook(id: str, data: dict, user: UserResponse = Depends(get_current_user)):
    logger.info(f"Renaming notebook: {id}")

    try:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE notebooks
                    SET notebook_name=%s
                    WHERE notebook_id=%s AND owner_id=%s
                """,
                    (data["notebook_name"], id, user.user_id),
                )
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Notebook not found")

        logger.info(f"Notebook renamed: {id}")
        return {"message": "updated"}

    except HTTPException:
        raise
    except KeyError:
        logger.warning("Missing notebook_name in request body")
        raise HTTPException(status_code=400, detail="notebook_name is required")

    except Exception as e:
        logger.exception(f"Error renaming notebook: {id}")
        raise HTTPException(status_code=500, detail="Failed to update notebook")


@router.delete("/api/notebooks/{id}")
def delete_notebook(id: str, user: UserResponse = Depends(get_current_user)):
    logger.info(f"Deleting notebook: {id}")

    try:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM notebooks WHERE notebook_id=%s AND owner_id=%s",
                    (id, user.user_id),
                )
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Notebook not found")
        logger.info(f"Notebook deleted: {id}")
        return {"message": "deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error deleting notebook: {id}")
        raise HTTPException(status_code=500, detail="Failed to delete notebook")