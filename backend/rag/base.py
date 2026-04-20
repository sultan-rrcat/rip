from abc import ABC, abstractmethod
class BaseRAG(ABC):
    @abstractmethod
    def retrieve_context(self, user_prompt: str) -> dict:
        pass