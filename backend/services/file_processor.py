import os

from core.logging import setup_logging
from core.dependencies import get_rag
from core.db import pg_connection
from rag.vector_rag import VectorRAG
from rag.graph_rag import GraphRAG
import config

logger = setup_logging()

def run_rag_pipeline(file_id: str, rag: GraphRAG, driver):
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
            embedding_ids = rag.store_chunks_and_embeddings(file_id, chunks, embeddings)
            logger.info(f"embeddings stored.")
        except Exception as e:
            logger.exception(f"Error storing embeddings. {e}")
            raise

        try:
            doc_type = rag.detect_document_type(chunks[0].page_content)
            logger.info(f"Document type detected: {doc_type}")
        except Exception:
            logger.warning("Doc type detection failed, defaulting to general")
            doc_type = "general"

        try:
            entities = [rag.extract_entities(chunk.page_content, doc_type) for chunk in chunks]
            logger.info(f"Entities extracted for {len(chunks)} chunks")
        except Exception:
            logger.exception("Entity extraction failed")
            raise

        # store graph in Neo4j
        try:
            rag.store_graph(            
                notebook_id=notebook_id,
                file_id=file_id,
                file_name=file_name,
                chunks=chunks,
                doc_type=doc_type,
                entities=entities,
                embedding_ids = embedding_ids,
                driver=driver,
            )
            logger.info(f"Graph stored for file: {file_name}")
        except Exception:
            logger.exception("Graph storage failed")
            raise

        # update file status
        try:
            with pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE files SET file_status = 'ready' WHERE file_id = %s",
                        (file_id,),
                    )
        except Exception as e:
            logger.exception(f"Error updating file status: {e}")

    except Exception as e:
        logger.exception(f"Error: {e}")

        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE files SET file_status = 'error' WHERE file_id = %s",
                    (file_id,),
                )
