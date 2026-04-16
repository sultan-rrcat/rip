import os
import psycopg2
import requests as req
from fastapi import FastAPI, BackgroundTasks
from fastapi import UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import logging
import config
from routes import _notebooks_routes, _files_routes, _messages_routes
from dotenv import load_dotenv
from uuid import uuid4
from rag import RagPipeline
import json

from contextlib import asynccontextmanager
from fastapi import Request, Depends

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Initiating ML models...")
    app.state.rag = RagPipeline()
    logger.info("ML models loaded successfully.")

    yield #transfering control back to fastapi

    logger.info(f"Shutting down and clearing models...")
    app.state.rag = None


load_dotenv()
LLM_URL = os.getenv("LLM_API_URL", "http://10.10.30.65:8000")

logger = config.setup_logging()
logger.info("Logging has been successfully set up.")

app = FastAPI(lifespan=lifespan)
app.include_router(_notebooks_routes.router)
app.include_router(_files_routes.router)
app.include_router(_messages_routes.router)

# Enable CORS
# middleware - code that runs before and after every request
# CORS - Cross Origin Resource Sharing - Is a mechanism that allows to specify which other origins(domains, ports, protocols) are permitted to access their resources.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://10.31.2.94:5173",
    ],  # meaning only http://localhost:5173", "http://10.31.2.94:5173 can make request here.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_rag(request: Request) -> RagPipeline:
    return request.app.state.rag



class PromptRequest(BaseModel):
    prompt: str
    notebook_id: str

def ask_qwen(prompt: str) -> str:
    logger.info(f"Prompt send to LLM: {prompt}")
    response = req.post(
        f"{LLM_URL}/v1/chat/completions",
        json={
            "model": "qwen2.5-coder-14b",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024,
        },
        proxies={"http": None, "https": None},
    )
    return response.json()["choices"][0]["message"]["content"]

def stream_qwen(prompt: str):
    logger.info(f"Prompt send to LLM: {prompt}")

    response = req.post(
        f"{LLM_URL}/v1/chat/completions",
        json={
            "model": "qwen2.5-coder-14b",
            "messages": [{"role": "user", "content": prompt}],
            "stream": True
        },
        stream=True,
        proxies={"http": None, "https": None},
    )

    for line in response.iter_lines():
        if line:
            decoded = line.decode("utf-8")

            if decoded.startswith("data:"):
                payload = decoded[5:].strip()

                if payload == "[DONE]":
                    break

                try:
                    chunk = json.loads(payload)
                    token = chunk["choices"][0]["delta"].get("content", "")
                    if token:
                        yield token
                except Exception as e:
                    logger.error(f"Stream parsing error: {e}")

def save_messages(notebook_id, user_prompt, response_text):
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            # Insert user message
            cur.execute(
                """
                INSERT INTO messages (notebook_id, role, text)
                VALUES (%s, %s, %s)
            """,
                (notebook_id, "user", user_prompt),
            )

            # Insert assistant response
            cur.execute(
                """
                INSERT INTO messages (notebook_id, role, text)
                VALUES (%s, %s, %s)
            """,
                (notebook_id, "assistant", response_text),
            )

@app.get("/api/health")
def health():
    return {"status": "ok"}

def format_context_for_llm(context_json):
    chunks = []
    for i, item in enumerate(context_json.get("results", []), 1):
        chunks.append(
            f"[{i} ({item['source']})]\n" f"{item['section']}\n" f"{item['content']}"
        )
    return "\n\n".join(chunks)

def extract_sources(context_json):
    sources = []
    for item in context_json.get("results", []):
        sources.append(
            {
                "source": item.get("source"),
                "section": item.get("section"),
            }
        )
    unique_sources = [dict(t) for t in {tuple(d.items()) for d in sources}]

    return unique_sources

def get_last_messages(notebook_id: str, limit: int = 6):
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role, text
                FROM messages
                WHERE notebook_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (notebook_id, limit),
            )
            rows = cur.fetchall()
    return list(reversed(rows))

def format_chat_history(messages):
    history = []
    for role, text in messages:
        if role == "user":
            history.append(f"User: {text}")
        else:
            history.append(f"Assistant: {text}")

    return "\n".join(history)

def rewrite_prompt(notebook_id: str, user_prompt: str) -> str:
    messages = get_last_messages(notebook_id)

    # If no history → return original prompt
    if not messages:
        return user_prompt

    history_text = format_chat_history(messages)

    # Prompt engineering for rewriting
    rewrite_instruction = f"""
    You are a query rewriter.

    Your task is to rewrite the user's query ONLY.

    Rules:
    - Do NOT answer
    - Do NOT greet
    - Do NOT add explanations
    - Output MUST be a single rewritten query
    - If the query is already clear, return it unchanged

    Conversation History:
    {history_text}

    Latest User Query:
    {user_prompt}

    Rewritten Query:
    """

    try:
        rewritten_prompt = ask_qwen(rewrite_instruction)
        logger.info(f"Rewrittend User Query: {rewritten_prompt}")
        return rewritten_prompt.strip()
    except Exception as e:
        logger.error(f"Error rewriting prompt: {e}")
        return user_prompt  # fallback

@app.post("/api/prompt")
def prompt(request: PromptRequest, rag: RagPipeline = Depends(get_rag)):
    user_prompt = request.prompt
    try:
        # rag = RagPipeline()
        logger.info("RAG pipeline initialized - For Prompt")
        try:
            context_json = rag.retrieve_context(user_prompt)
            logger.info(f"context_json: {context_json}")

            formatted_context = format_context_for_llm(context_json)
            sources = extract_sources(context_json)
            logger.info(f"Context retrieved successfully for prompt: {user_prompt}")
        except Exception as e:
            logger.error(
                f"Error retrieving context for prompt: {user_prompt}. Exception: {e}"
            )
            formatted_context = ""  # fallback to empty context
            sources = []

    except Exception as e:
        logger.error(f"Error initiating RAG pipeline. Exception: {e}")
        return {"response": "Pipeline initialization failed."}

    rewritten_user_prompt = rewrite_prompt(request.notebook_id, user_prompt)

    # --- Prompt engineering: combine context with user prompt ---
    enriched_prompt = f"""
    You are a helpful assistant.
    Use the following context to answer the user query:"

    Context:
    {formatted_context}

    User Query:
    {rewritten_user_prompt}
    """

    response_text = ask_qwen(enriched_prompt)

    return {"chatbot_response": response_text, "sources": sources}

@app.post("/api/prompt/stream")
def prompt_stream(request: PromptRequest, rag: RagPipeline = Depends(get_rag)):
    user_prompt = request.prompt

    def generate():
        try:
            try:
                context_json = rag.retrieve_context(user_prompt)
                formatted_context = format_context_for_llm(context_json)
                sources = extract_sources(context_json)
            except Exception:
                formatted_context = ""

            rewritten_prompt = rewrite_prompt(request.notebook_id, user_prompt)

            enriched_prompt = f"""
    You are a helpful assistant.

    Context:
    {formatted_context}

    User Query:
    {rewritten_prompt}
    """

            full_response = []

            yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
            for token in stream_qwen(enriched_prompt):
                full_response.append(token)

                # SSE format
                
                yield f"data: {json.dumps({'response': token})}\n\n"

            yield "data: [DONE]\n\n"

            # Save after stream completes
            save_messages(
                request.notebook_id,
                user_prompt,
                "".join(full_response)
            )

        except Exception as e:
            logger.error(f"Streaming error: {e}")
            sources = []
            yield f"data: {json.dumps({'type': 'error', 'message': 'Streaming failed'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

@app.post("/api/files/upload")
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
    with config.pg_connection() as conn:
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

def run_rag_pipeline(file_id: str, rag: RagPipeline = Depends(get_rag)):
    try:
        with config.pg_connection() as conn:
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
            raisea
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
        with config.pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE files SET file_status = 'ready' WHERE file_id = %s",
                    (file_id,),
                )
    except Exception as e:
        logger.exception(f"Error: {e}")

        with config.pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE files SET file_status = 'error' WHERE file_id = %s",
                    (file_id,),
                )

@app.post("/api/files/{file_id}/process")
def process_file(file_id: str, background_tasks: BackgroundTasks, rag: RagPipeline = Depends(get_rag)):
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM files WHERE file_id=%s", (file_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="File not found")

    background_tasks.add_task(run_rag_pipeline, file_id, rag)
    return {"message": "processing started"}
