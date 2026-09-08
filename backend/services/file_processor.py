# file_processor.py 

import os
import asyncio
from core.logging import setup_logging
from core.db import pg_connection
from rag.vector_rag import VectorRAG
import config

logger = setup_logging()

async def run_rag_pipeline(file_id: str, rag: VectorRAG):
    try:
        # ─── Fetch metadata ───
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT notebook_id, file_name FROM files WHERE file_id=%s",
                    (file_id,),
                )
                result = cur.fetchone()

        if not result:
            raise Exception(f"File not found: {file_id}")

        notebook_id = str(result[0])
        file_name = result[1]
        file_path = os.path.join(config.UPLOAD_DIR, notebook_id, f"{file_id}.pdf")

        logger.info(f"Starting pipeline: {file_name}")

        # ─── Blocking steps → offload to thread ───
        try:
            documents = await asyncio.to_thread(rag.document_loader, file_path)
            logger.info("Document loaded")
        except Exception:
            logger.exception("Document loading failed")
            raise

        try:
            chunks = await asyncio.to_thread(rag.chunk_documents, documents, file_name)
            logger.info(f"Chunks created: {len(chunks)}")
        except Exception:
            logger.exception("Chunking failed")
            raise

        try:
            embeddings = await asyncio.to_thread(rag.generate_embeddings, chunks)
            logger.info("Embeddings generated")
        except Exception:
            logger.exception("Embedding failed")
            raise

        try:
            await asyncio.to_thread(
                rag.store_chunks_and_embeddings, file_id, chunks, embeddings
            )
            logger.info("Embeddings stored")
        except Exception:
            logger.exception("Embedding storage failed")
            raise

        # ─── Mark ready ───
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE files SET file_status = 'ready' WHERE file_id = %s",
                    (file_id,),
                )
        logger.info(f"File ready: {file_id}")

    except Exception:
        logger.exception(f"Pipeline failed: {file_id}")
        try:
            with pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE files SET file_status = 'error' WHERE file_id = %s",
                        (file_id,),
                    )
        except Exception:
            logger.exception(f"Failed to mark error status: {file_id}")