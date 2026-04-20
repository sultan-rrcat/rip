from fastapi import Request
# from rag.pipeline import RagPipeline
from rag.graph_rag import GraphRAG
from rag.vector_rag import VectorRAG

def get_rag(request: Request) -> VectorRAG:
    return request.app.state.rag
