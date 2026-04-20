# RAG Chatbot Assistant (NotebookLM-style)

## Context
Building a RAG chatbot assistant similar to Google's NotebookLM for a physics R&D organization.  
This system is intended for **internal, offline use only**.

---

## Tech Stack

### Frontend
- React
- Tailwind CSS

### Backend
- FastAPI
- PostgreSQL with pgvector

---

## Models

- **LLM:** qwen2.5-14B  
- **Document Ingestion:** docling  
- **Embeddings:** bge-m3  
- **Reranking:** bge_reranker_v2_m3  

---

## 🧪 Git Workflow

Follow this workflow for all feature development:

```bash
# Ensure main is up to date
git checkout main
git pull origin main

# Create a new feature branch
git checkout -b feature/<feature-name>

# Work on your changes
git add .
git commit -m "your message"

# Push branch to remote
git push --set-upstream origin feature/<feature-name>

# Rebase onto latest main (clean history)
git rebase main

# Merge into main
git checkout main
git merge feature/<feature-name>

# Push updated main
git push origin main
```


=====

