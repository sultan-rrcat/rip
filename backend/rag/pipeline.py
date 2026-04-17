from sentence_transformers import SentenceTransformer, CrossEncoder
import config
from langchain_opendataloader_pdf import OpenDataLoaderPDFLoader
from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_experimental.text_splitter import SemanticChunker
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_core.documents import Document
from docling.document_converter import DocumentConverter
from uuid import uuid4
from psycopg2.extras import execute_values, Json
import json
import os

from core.logging import setup_logging
from core.db import pg_connection

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
            logger.warn(f"⚠️ Docling failed, fallback to OpenDataLoader: {e}")

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

            logger.info(f"[Step 1] Final chunks (Markdown Splitter): {len(final_chunks)}")
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

    def store_chunks_and_embeddings(self, file_id, chunks, embeddings):

        def parse_metadata(metadata):
            if isinstance(metadata, str):
                try:
                    return json.loads(metadata)
                except:
                    return {"raw": metadata}
            return metadata

        try:
            # Prepare data tuple for bulk insertion
            data = [
                (
                    file_id,
                    doc.page_content,
                    embedding,
                    Json(parse_metadata(doc.metadata)),
                )
                for doc, embedding in zip(chunks, embeddings)
            ]

            with pg_connection() as conn:
                with conn.cursor() as cur:
                    query = """
                        INSERT INTO embeddings_test (file_id, chunk_text, embedding, metadata)
                        VALUES %s
                    """
                    # execute_values batches the inserts instantly
                    execute_values(cur, query, data)

                conn.commit()  # Don't forget to commit!

            logger.info(f"✅ Stored {len(data)} embeddings in bulk.")

        except Exception as e:
            logger.exception(f"Error while storing embeddings: {e}")
            raise

    def retrieve_context(
        self, user_prompt, top_k=5, vector_threshold=0.1, rerank_threshold=0.05
    ):
        try:
            # logger.info(f"User Prompt: {user_prompt[:50]}...")

            prompt_embeddings = self.embedding_model.embed_query(user_prompt)

            # Vector and Keyword Search
            with pg_connection() as conn:
                with conn.cursor() as cur:
                    # 1. Vector Search
                    cur.execute(
                        """
                        SELECT chunk_text, metadata, 1-(embedding<=>%s::vector) as similarity
                        FROM embeddings_test
                        ORDER BY embedding<=>%s::vector
                        LIMIT %s
                        """,
                        (prompt_embeddings, prompt_embeddings, top_k),
                    )
                    vector_results = cur.fetchall()
                    logger.info(f"Vector search returned {len(vector_results)} raw results")
                    # logger.info(f"Vector search results: \n{vector_results}")

                    # 2. Keyword (Full Text) Search
                    cur.execute(
                        """
                        SELECT chunk_text, metadata, 
                            ts_rank_cd(to_tsvector('english', chunk_text), websearch_to_tsquery('english', %s)) AS rank
                        FROM embeddings_test
                        WHERE to_tsvector('english', chunk_text) @@ websearch_to_tsquery('english', %s)
                        ORDER BY rank DESC
                        LIMIT %s
                        """,
                        (user_prompt, user_prompt, top_k),
                    )
                    keyword_results = cur.fetchall()
                    logger.info(f"Keyword search returned {len(keyword_results)} raw results")
                    # logger.info(f"Keyword search result : {keyword_results}")

            # Filtering Vector Results
            initial_count = len(vector_results)
            vector_results = [r for r in vector_results if r[2] > vector_threshold]
            logger.info(
                f"Filtered vector results from {initial_count} down to {len(vector_results)} (threshold > {vector_threshold})"
            )

            # Hybrid Formatting
            contexts = {}

            # VECTOR RESULTS
            for i, (text, metadata, score) in enumerate(vector_results):
                key = hash(text)

                contexts[key] = {
                    "text": text,
                    "metadata": metadata,
                    "vector_rank": i + 1,
                    "keyword_rank": None,
                }

            # KEYWORD RESULTS
            for i, (text, metadata, score) in enumerate(keyword_results):
                key = hash(text)

                if key not in contexts:
                    contexts[key] = {
                        "text": text,
                        "metadata": metadata,
                        "vector_rank": None,
                        "keyword_rank": i + 1,
                    }
                else:
                    contexts[key]["keyword_rank"] = i + 1

            # logger.info(f"Hybrid formatted context: \n{contexts}")

            # RRF Implementation
            k = 60
            for c in contexts.values():
                rrf_score = 0
                if c["vector_rank"] is not None:
                    rrf_score += 1 / (k + c["vector_rank"])
                if c["keyword_rank"] is not None:
                    rrf_score += 1 / (k + c["keyword_rank"])
                c["rrf_score"] = rrf_score

            # Initial Sort after Hybrid Merge
            sorted_contexts = sorted(
                contexts.values(), key=lambda x: x["rrf_score"], reverse=True
            )
            logger.info(f"Total unique contexts merged: {len(sorted_contexts)}")
            # logger.info(f"RRF Contexts : {sorted_contexts}")

            # Reranking
            rerank_k = top_k * 3
            rerank_subset = sorted_contexts[:rerank_k]

            if rerank_subset:
                # logger.info(f"Sending {len(rerank_subset)} candidates to reranker model")
                pairs = [(user_prompt, str(c.get("text", ""))) for c in rerank_subset]
                scores = self.reranker_model.predict(pairs)

                for i, score in enumerate(scores):
                    rerank_subset[i]["rerank_score"] = score

                # Sort by rerank score
                rerank_subset.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)

                # Re-combine (keeping top reranked items first)
                final_list = rerank_subset + sorted_contexts[rerank_k:]
            else:
                logger.info("No results found to rerank.")
                final_list = sorted_contexts

            # logger.info(f"Rerank subset: {rerank_subset}")

            # Adding threshold
            # Final Formatting
            # top_results = final_list[:top_k]
            filtered = [
                c for c in final_list if c.get("rerank_score", 0) > rerank_threshold
            ]

            if not filtered:
                logger.warning("⚠️ No reranked results passed threshold — using fallback")
                filtered = final_list[:3]

            top_results = filtered

            logger.info(f"Retrieved top {len(top_results)} final contexts")

            structured_context = {
                "query": user_prompt,
                "results": [
                    {
                        "content": c["text"],
                        "source": c["metadata"].get("source", "unknown"),
                        "section": " > ".join(
                            filter(
                                None,
                                [
                                    c["metadata"].get("H1"),
                                    c["metadata"].get("H2"),
                                    c["metadata"].get("H3"),
                                ],
                            )
                        ),
                        "rerank_score": c['rerank_score']
                    }
                    for c in top_results
                ],
            }
            logger.info(f"structured_context: {structured_context}")
            return structured_context

            # return final_context

        except Exception as e:
            logger.exception(f"Error during context retrieval: {str(e)}")
            raise


# =========================
# 🚀 RUN PIPELINE
# =========================
if __name__ == "__main__":
    obj = RagPipeline()

    file_path = r"C:\Users\trainee\Desktop\Projects\CD_lab_report.pdf"
    file_id = str(uuid4())

    prompt = "what are the lease lines in RRCAT?"

    try:
        # documents = obj.document_loader(file_path)
        # chunks = obj.chunk_documents(documents)
        # embeddings = obj.generate_embeddings(chunks)
        # obj.store_chunks_and_embeddings(file_id, chunks, embeddings)
        context = obj.retrieve_context(prompt)
        logger.info(f"Prompt: {prompt}\n Context: {context}")
        logger.info("🎉 Pipeline completed")

    except Exception as e:
        logger.info(f"❌ Pipeline failed: {e}")
