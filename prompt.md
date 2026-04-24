
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

- **LLM:** qwen2.5-14B (10.10.30.65) 
- **Document Ingestion:** docling  
- **Embeddings:** bge-m3  
- **Reranking:** bge_reranker_v2_m3  

**Folder Structure**
```
└── 📁backend
    └── 📁__pycache__
        ├── app.cpython-311.pyc
        ├── config.cpython-311.pyc
        ├── rag.cpython-311.pyc
    └── 📁core
        └── 📁__pycache__
            ├── __init__.cpython-311.pyc
            ├── db.cpython-311.pyc
            ├── dependencies.cpython-311.pyc
            ├── logging.cpython-311.pyc
            ├── prompts.cpython-311.pyc
        ├── __init__.py
        ├── db.py
        ├── dependencies.py
        ├── logging.py
        ├── prompts.py
    └── 📁logs
        ├── app.log
    └── 📁rag
        └── 📁__pycache__
            ├── __init__.cpython-311.pyc
            ├── config.cpython-311.pyc
            ├── graph_rag.cpython-311.pyc
            ├── pipeline.cpython-311.pyc
            ├── vector_rag.cpython-311.pyc
        ├── __init__.py
        ├── agentic_rag.py
        ├── base.py
        ├── graph_rag.py
        ├── pipeline.py
        ├── vector_rag.py
    └── 📁routes
        └── 📁__pycache__
            ├── _files_routes.cpython-311.pyc
            ├── _messages_routes.cpython-311.pyc
            ├── _notebooks_routes.cpython-311.pyc
            ├── files.cpython-311.pyc
            ├── llm.cpython-311.pyc
            ├── messages.cpython-311.pyc
            ├── notebooks.cpython-311.pyc
        ├── files.py
        ├── llm.py
        ├── messages.py
        ├── notebooks.py
    └── 📁services
        └── 📁__pycache__
            ├── __init__.cpython-311.pyc
            ├── chat.cpython-311.pyc
            ├── file_processor.cpython-311.pyc
            ├── llm.cpython-311.pyc
            ├── rewritter.cpython-311.pyc
        ├── __init__.py
        ├── chat.py
        ├── file_processor.py
        ├── llm.py
        ├── rewritter.py
    └── 📁uploads
        └── 📁18982f6d-9c14-438e-b5c5-3fb10aede81d
            ├── aad0066e-bde6-4659-8a53-adf8dd868788.pdf
            ├── db609900-8296-4065-aa8b-05efcb825692.pdf
        └── 📁20763e39-767f-43d5-8c1d-758ec4ea6e18
            ├── ef9f430a-5342-4636-89f8-bd2318d6d038.pdf
            ├── f652c817-f302-408b-a737-f24a64ad5f56.pdf
        
    ├── app.py
    ├── config.py
    ├── download.py
    └── schema.sql
```
