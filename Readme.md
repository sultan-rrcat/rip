Context:
Building a RAG chatbot assistant like Google's NotebookLM for a physics R&D organization for internal (offline) use only.

Tech Stack:
frontend: react + tailwind
backend: fastapi + pgvector

Models:
qwen2.5-14B - as LLM
docling - for document ingestion
allminilm-l6-v2 - for embeddings
bge_reranker_v2_m3 - for reranking

Tools:
Langchain - MarkdownHeaderTextSplitter
sentence_transformers


pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121