import os
import psycopg2
import requests as req
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
import config
from dotenv import load_dotenv

load_dotenv()


DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")  # default if not set
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
LLM_URL = os.getenv("LLM_API_URL", "http://10.10.30.65:8000")


logger = config.setup_logging()
logger.info("Logging has been successfully set up.")

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request body schema
class PromptRequest(BaseModel):
    prompt: str


def get_db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )


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


def save_messages(user_prompt, response_text):
    with get_db() as conn:
        with conn as cur:
            cur.execute(
                "INSERT INTO prompts (prompt, response) VALUES (%s, %s)",
                (user_prompt, response_text),
            )


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/prompt")
def prompt(request: PromptRequest):
    user_prompt = request.prompt
    response_text = ask_qwen(user_prompt)
    save_messages(user_prompt, response_text)
    return {"response": response_text}


# Run with: uvicorn main:app --host 0.0.0.0 --port 5000
