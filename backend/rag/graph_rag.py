from rag.pipeline import RagPipeline
from core.prompts import extraction_prompt
from core.logging import setup_logging

logger = setup_logging()

class GraphRAG(RagPipeline):
    def retrieve_context(self, user_prompt):
        #vector search + keyword search + rrf + reranking 
        pass