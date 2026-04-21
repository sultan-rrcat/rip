from sentence_transformers import SentenceTransformer, CrossEncoder

# import config
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
import asyncio
import httpx
from textwrap import dedent

from backend.core.logging import setup_logging
from backend.core.db import pg_connection
from backend.core.prompts import DETECTION_PROMPT
from backend.core.prompts import EXTRACTION_PROMPTS

from backend import config

logger = setup_logging()


class RagPipeline:
    def __init__(self):
        # self.embedding_model = HuggingFaceEmbeddings(model_name=config.BGE_M3_MODEL_PATH)
        # self.reranker_model = CrossEncoder(config.BGE_RERANKER_V2_M3)
        pass

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

    # =========================
    # 🔍 DOCUMENT TYPE DETECTION
    # =========================
    def detect_document_type(self, sample_text: str) -> str:
        prompt = DETECTION_PROMPT.format(text=sample_text[:1500])
        try:
            # with httpx.Client(timeout=300.0) as client:
            #     response = client.post(
            #         f"{config.OLLAMA_URL}/api/generate",
            #         json={
            #             "model": config.OLLAMA_EXTRACTION_MODEL,
            #             "prompt": prompt,
            #             "stream": False,
            #             "think": False
            #         },
            #     )
            #     response.raise_for_status()

            #     data = response.json()
            #     raw_text = data.get("response", "").strip()

            #     print(f"DEBUG: Classified as: {raw_text}")
            #     return raw_text

            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    f"{config.LLM_URL}/v1/chat/completions",
                    json={
                        "model": "qwen2.5-coder-14b",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 256,
                    },
                )

                response.raise_for_status()
                data = response.json()["choices"][0]["message"]["content"]
                raw = data.strip()

                # strip markdown fences if model wraps in ```json
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]

                parsed = json.loads(raw)
                doc_type = parsed.get("doc_type", "general")

                # print(f"DEBUG: Classified as: {raw} - doc type: {doc_type}")
                return doc_type

        except Exception as e:
            logger.error(f"Document type detection failed: {e}")
            return "general"

    def extract_entities(self, chunk_text: str, doc_type: str) -> dict:
        prompt_template = EXTRACTION_PROMPTS.get(doc_type, EXTRACTION_PROMPTS["general"])
        prompt = prompt_template.format(text=chunk_text)
        try:
            with httpx.Client(timeout=300.0) as client:
                response = client.post(
                    f"{config.LLM_URL}/v1/chat/completions",
                    json={
                        "model": "qwen2.5-coder-14b",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 1024,
                    },
                )
                response.raise_for_status()
                data = response.json()["choices"][0]["message"]["content"]
                raw = data.strip()

                logger.info(raw)

                # strip markdown fences if model wraps in ```json
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]

                parsed = json.loads(raw)
                entities = parsed.get("entities", [])
                relationships = parsed.get("relationships", [])

                logger.info(
                    f"Extracted {len(entities)} entities & {len(relationships)} relationships."
                )
                return {"entities": entities, "relationships": relationships}
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parsed failed for chunk extraction: {e}")
            return {"entities": [], "relationships": []}

        except Exception as e:
            logger.warning(f"Entity extraction failed. {e}")
            return {"entities": [], "relationships": []}

    def store_graph(self, file_id: str, chunks: list, neo4j_driver) -> None:
        """
        Build Neo4j graph for a file:
        - Detect document type (1 LLM call)
        - Extract entities per chunk concurrently (N async LLM calls)
        - Store nodes and edges in Neo4j
        """
        pass


# =========================
# 🚀 RUN PIPELINE
# =========================
if __name__ == "__main__":
    obj = RagPipeline()

    file_path = r"C:\Users\trainee\Desktop\Projects\CD_lab_report.pdf"
    file_id = str(uuid4())

    chunk = dedent("""
    SLURM is an open-source job scheduler used for managing and allocating resources in highperformance computing (HPC) environments. It is responsible for:  
- [ ] Scheduling and dispatching compute jobs to nodes.  
- [ ] Managing queues of submitted jobs and prioritizing their execution.  
- [ ] Monitoring resource usage and ensuring efficient utilization.
    """)

    try:
        # documents = obj.document_loader(file_path)
        # chunks = obj.chunk_documents(documents)
        # embeddings = obj.generate_embeddings(chunks)
        # obj.store_chunks_and_embeddings(file_id, chunks, embeddings)
        # context = obj.retrieve_context(prompt)
        # logger.info(f"Prompt: {prompt}\n Context: {context}")
        # logger.info("🎉 Pipeline completed")
        doc_type = obj.detect_document_type(chunk)
        print(doc_type)

        ent_rel = obj.extract_entities(chunk, f"{doc_type}")
        print(ent_rel)

    except Exception as e:
        logger.info(f"❌ Pipeline failed: {e}")
