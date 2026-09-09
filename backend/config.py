import os

BGE_M3_MODEL_PATH = os.getenv("BGE_M3_MODEL_PATH", "/models/bge-m3")
BGE_RERANKER_V2_M3 = os.getenv(
    "BGE_RERANKER_V2_M3",
    "/models/reranker/bge_reranker_v2_m3",
)
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/app/uploads")

OLLAMA_EXTRACTION_MODEL = "qwen3.5"
OLLAMA_URL = "http://localhost:11434"
LLM_URL = os.getenv("LLM_URL")