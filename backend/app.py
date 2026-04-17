import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from routes import notebooks, files, messages, llm
from dotenv import load_dotenv
from rag.pipeline import RagPipeline
from core.logging import setup_logging
from contextlib import asynccontextmanager
import httpx

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

logger = setup_logging()
logger.info("Logging has been successfully set up.")

app = FastAPI(lifespan=lifespan)
app.include_router(notebooks.router)
app.include_router(files.router)
app.include_router(messages.router)
app.include_router(llm.router)

# Enable CORS
# middleware - code that runs before and after every request
# CORS - Cross Origin Resource Sharing - Is a mechanism that allows to specify which other origins(domains, ports, protocols) are permitted to access their resources.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # meaning only http://localhost:5173", "http://10.31.2.94:5173 can make request here.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"status": "ok"}
