# file_processor.py 

import os
import asyncio
from core.logging import setup_logging
from core.dependencies import get_rag
from core.db import pg_connection
from rag.vector_rag import VectorRAG
from rag.graph_rag import GraphRAG
import config

logger = setup_logging()

async def run_rag_pipeline(file_id: str, rag: GraphRAG, driver):
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
            embedding_ids = await asyncio.to_thread(
                rag.store_chunks_and_embeddings, file_id, chunks, embeddings
            )
            logger.info("Embeddings stored")
        except Exception:
            logger.exception("Embedding storage failed")
            raise

        # ─── Doc type detection ───
        try:
            doc_type = await asyncio.to_thread(
                rag.detect_document_type, chunks[0].page_content
            )
            logger.info(f"Doc type: {doc_type}")
        except Exception:
            logger.warning("Doc type detection failed, defaulting to general")
            doc_type = "general"

        # ─── Entity extraction — all chunks concurrently ───
        try:
            semaphore = asyncio.Semaphore(10)
            
            entities = await asyncio.gather(*[
                rag.extract_entities_async(chunk.page_content, doc_type, semaphore)
                for chunk in chunks
            ])
            entities = list(entities)
            logger.info(f"Entities extracted: {len(chunks)} chunks")
        except Exception:
            logger.exception("Entity extraction failed")
            raise

        # ─── Store graph ───
        try:
            await asyncio.to_thread(
                rag.store_graph,
                notebook_id=notebook_id,
                file_id=file_id,
                file_name=file_name,
                chunks=chunks,
                doc_type=doc_type,
                entities=entities,
                embedding_ids=embedding_ids,
                driver=driver,
            )
            logger.info(f"Graph stored: {file_name}")
        except Exception:
            logger.exception("Graph storage failed")
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