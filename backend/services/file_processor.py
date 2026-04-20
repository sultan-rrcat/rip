import os

from core.logging import setup_logging
from core.dependencies import get_rag
from core.db import pg_connection
from rag.vector_rag import VectorRAG
import config

logger = setup_logging()

def run_rag_pipeline(file_id: str, rag: VectorRAG):
    try:
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT notebook_id, file_name FROM files WHERE file_id=%s",
                    (file_id,),
                )
                result = cur.fetchone()
                notebook_id = result[0]
                file_name = result[1]

        if not result:
            raise Exception("File not found")
        file_path = os.path.join(config.UPLOAD_DIR, notebook_id, f"{file_id}.pdf")

        logger.info(f"Starting Processing file: {file_name}")

        try:
            documents = rag.document_loader(file_path)
            logger.info(f"Document loader processed.")
        except Exception as e:
            logger.exception(f"Error loading document: {e}")
            raise
        try:
            chunks = rag.chunk_documents(documents, file_name)
            logger.info(f"Chunks created.")
        except Exception as e:
            logger.exception(f"Error chunking document. {e}")
            raise
        try:
            embeddings = rag.generate_embeddings(chunks)
            logger.info(f"embeddings generated.")
        except Exception as e:
            logger.exception(f"Eror generating embeddings. {e}")
            raise
        try:
            rag.store_chunks_and_embeddings(file_id, chunks, embeddings)
            logger.info(f"embeddings stored.")
        except Exception as e:
            logger.exception(f"Error storing embeddings. {e}")
            raise

        # try:
        #     context = rag.retrieve_context(user_prompt)
        # except Exception as e:
        #     logger.exception(f"Error retrieving context. {e}")
        #     raise

        # update file status
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE files SET file_status = 'ready' WHERE file_id = %s",
                    (file_id,),
                )
    except Exception as e:
        logger.exception(f"Error: {e}")

        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE files SET file_status = 'error' WHERE file_id = %s",
                    (file_id,),
                )
