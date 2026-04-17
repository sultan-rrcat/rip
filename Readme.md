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

Git workflow:

git checkout main
git pull origin main
git checkout -b feature/new-branch
- start working on the branch
git add .
git commit -m "messages"
git push origin feature/new-branch
git rebase main -doing this will rewrite git history - so the new feature branch commits appear as if they were made directly on top of the latest main.
git checkout main
git merge feature/new-branch
git push origin main