from fastapi import Request
from app.rag.vector_rag import VectorRAG

def get_rag(request: Request) -> VectorRAG:
    return request.app.state.rag
