from fastapi import Request
from rag.pipeline import RagPipeline

def get_rag(request: Request) -> RagPipeline:
    return request.app.state.rag