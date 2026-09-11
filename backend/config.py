import os

# Get the absolute path of the backend directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BGE_M3_MODEL_PATH = os.getenv(
    "BGE_M3_MODEL_PATH", os.path.join(BASE_DIR, "models", "bge-m3")
)
BGE_RERANKER_V2_M3 = os.getenv(
    "BGE_RERANKER_V2_M3",
    os.path.join(BASE_DIR, "models", "reranker", "bge_reranker_v2_m3"),
)
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))

LLM_URL = os.getenv("LLM_URL")

