from fastapi import Request
# from rag.pipeline import RagPipeline
from rag.graph_rag import GraphRAG
from rag.vector_rag import VectorRAG
from neo4j import Driver

def get_rag(request: Request) -> GraphRAG:
    return request.app.state.rag

def get_neo4j(request: Request) -> Driver:
    return request.app.state.neo4j