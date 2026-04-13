import os
import psycopg2
import requests as req
from fastapi import FastAPI, BackgroundTasks
from fastapi import UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import logging
import config
from routes import _notebooks_routes, _files_routes, _messages_routes
from dotenv import load_dotenv
from uuid import uuid4
from rag import RagPipeline

load_dotenv()
LLM_URL = os.getenv("LLM_API_URL", "http://10.10.30.65:8000")

logger = config.setup_logging()
logger.info("Logging has been successfully set up.")

app = FastAPI()
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


class PromptRequest(BaseModel):
    prompt: str


def ask_qwen(prompt: str) -> str:
    logger.info(f"Prompt send to LLM: {prompt}")
    response = req.post(
        f"{LLM_URL}/v1/chat/completions",
        json={
            "model": "qwen2.5-coder-14b",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512,
        },
        proxies={"http": None, "https": None},
    )
    return response.json()["choices"][0]["message"]["content"]


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


@app.post("/api/prompt")
def prompt(request: PromptRequest):
    user_prompt = request.prompt
    response_text = ask_qwen(user_prompt)
    # save_messages(request.notebook_id, user_prompt, response_text)
    return {"response": response_text}


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


def run_rag_pipeline(file_id):
    try:
        try:
            rag = RagPipeline()
            logger.info(f"RAG pipeline initialized.")
        except Exception as e:
            logger.info(f"Error initiating rag pipeline. {e}")
            return 

        with config.pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT notebook_id FROM files WHERE file_id=%s",
                    (file_id,)
                )
                result = cur.fetchone()
                notebook_id = result[0]


        if not result:
            raise Exception("File not found")
        file_path = os.path.join(config.UPLOAD_DIR, notebook_id, f"{file_id}.pdf")

        documents = rag.document_loader(file_path)
        if not documents:
            raise Exception("Document loading failed")
        chunks = rag.chunk_documents(documents)
        embeddings = rag.generate_embeddings(chunks)
        rag.store_chunks_and_embeddings(file_id, chunks, embeddings)
        # update file status
        with config.pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE files SET file_status = 'ready' WHERE file_id = %s",
                    (file_id,),
                    )
    except Exception as e:
        print(f"Error: {e}")

        with config.pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE files SET file_status = 'error' WHERE file_id = %s",
                    (file_id,)
                )


@app.post("/api/files/{file_id}/process")
def process_file(file_id: str, background_tasks: BackgroundTasks):
    with config.pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM files WHERE file_id=%s", (file_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="File not found")

    background_tasks.add_task(run_rag_pipeline, file_id)
    return {"message": "processing started"}
