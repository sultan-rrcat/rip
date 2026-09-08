
# pipeline.py 


from sentence_transformers import CrossEncoder

# import config
from langchain_opendataloader_pdf import OpenDataLoaderPDFLoader
from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_core.documents import Document
from docling.document_converter import DocumentConverter
from psycopg2.extras import Json
import json


from core.logging import setup_logging
from core.db import pg_connection

import config

logger = setup_logging()


class RagPipeline:
    def __init__(self):
        self.embedding_model = HuggingFaceEmbeddings(
            model_name=config.BGE_M3_MODEL_PATH
        )
        self.reranker_model = CrossEncoder(config.BGE_RERANKER_V2_M3)

    # =========================
    # 📄 DOCUMENT LOADER
    # =========================
    def document_loader(self, file):
        try:
            # 🔥 Primary: Docling
            converter = DocumentConverter()
            result = converter.convert(file)

            md_text = result.document.export_to_markdown()

            logger.info("✅ Loaded with Docling")

            return [
                Document(
                    page_content=md_text, metadata={"source": file, "loader": "docling"}
                )
            ]

        except Exception as e:
            logger.warning(f"⚠️ Docling failed, fallback to OpenDataLoader: {e}")

            try:
                loader = OpenDataLoaderPDFLoader(file, format="markdown")
                documents = loader.load_and_split()

                logger.info(f"✅ Loaded with OpenDataLoader: {len(documents)} pages")

                return documents  # already Document objects

            except Exception as e:
                logger.info(f"❌ Both loaders failed: {e}")
                raise

    # =========================
    # ✂️ CHUNKING
    # =========================
    def chunk_documents(self, documents, file_name):
        try:
            # STEP 1: Markdown structure
            headers_to_split_on = [
                ("#", "H1"),
                ("##", "H2"),
                ("###", "H3"),
            ]

            md_splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=headers_to_split_on
            )

            final_chunks = []

            for doc in documents:
                structured_docs = md_splitter.split_text(doc.page_content)

                for chunk in structured_docs:
                    # 🔥 Extract clean filename
                    # source = doc.metadata.get("source", "")
                    # file_name = os.path.basename(source) if source else "unknown"

                    # 🔥 Extract headers safely
                    h1 = chunk.metadata.get("H1")
                    h2 = chunk.metadata.get("H2")
                    h3 = chunk.metadata.get("H3")

                    # 🔥 Build clean metadata
                    clean_metadata = {
                        "source": file_name,  # only filename, not full path
                    }

                    # Only include headers if they exist
                    if h1:
                        clean_metadata["H1"] = h1.strip()
                    if h2:
                        clean_metadata["H2"] = h2.strip()
                    if h3:
                        clean_metadata["H3"] = h3.strip()

                    final_chunks.append(
                        Document(
                            page_content=chunk.page_content.strip(),
                            metadata=clean_metadata,
                        )
                    )

            logger.info(
                f"[Step 1] Final chunks (Markdown Splitter): {len(final_chunks)}"
            )
            return final_chunks

            # STEP 2: Semantic chunking
            # semantic_splitter = SemanticChunker(self.embedding_model)

            # final_chunks = []

            # for doc in structured_docs:
            #     chunks = semantic_splitter.split_text(doc.page_content)

            #     for chunk in chunks:
            #         final_chunks.append(
            #             Document(
            #                 page_content=chunk,
            #                 metadata=doc.metadata  # preserve structure
            #             )
            #         )

            # logger.info(f"[Step 2] Final chunks: {len(final_chunks)}")

            # return structured_docs

        except Exception as e:
            logger.exception(f"Error splitting: {e}")
            raise

    # =========================
    # 🧠 EMBEDDINGS
    # =========================
    def generate_embeddings(self, chunks):
        try:
            texts = [doc.page_content for doc in chunks]
            embeddings = self.embedding_model.embed_documents(texts)
            return embeddings

        except Exception as e:
            logger.exception(f"Error while generating embeddings: {e}")
            raise

    # =========================
    # 💾 STORE
    # =========================

    def store_chunks_and_embeddings(self, file_id, chunks, embeddings) -> list[str]:

        def parse_metadata(metadata):
            if isinstance(metadata, str):
                try:
                    return json.loads(metadata)
                except:
                    return {"raw": metadata}
            return metadata

        embedding_ids = []

        try:
            with pg_connection() as conn:
                with conn.cursor() as cur:
                    for i, (doc, embedding) in enumerate(zip(chunks, embeddings)):
                        cur.execute(
                            """
                            INSERT INTO embeddings_test 
                                (file_id, chunk_index, chunk_text, embedding, metadata)
                            VALUES (%s, %s, %s, %s, %s)
                            RETURNING embedding_id
                            """,
                            (
                                file_id,
                                i,  # explicit index
                                doc.page_content,
                                embedding,
                                Json(parse_metadata(doc.metadata)),
                            ),
                        )
                        row = cur.fetchone()
                        embedding_ids.append(str(row[0]))

                conn.commit()

            logger.info(f"✅ Stored {len(embedding_ids)} embeddings.")
            return embedding_ids

        except Exception as e:
            logger.exception(f"Error storing embeddings: {e}")
            raise
