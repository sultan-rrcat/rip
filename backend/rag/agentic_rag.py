from rag.pipeline import RagPipeline
from rag.vector_rag import VectorRAG
from rag.graph_rag import GraphRAG
from core.logging import setup_logging

logger = setup_logging()

class AgenticRAG(RagPipeline):
    def __init__(self):
        self.vector_rag = VectorRAG()
        self.graph_rag = GraphRAG()
    def retrieve_context(self, user_prompt):
        pass