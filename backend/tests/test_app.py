"""Backend integration tests (real DB, real models, real APIs — no mocks).

Run from the repo root with the ``agent_env`` interpreter::

    ..\\ENV\\agent_env\\Scripts\\python.exe -m pytest

The session ``client`` fixture (see ``conftest.py``) applies ``schema.sql``,
boots the real FastAPI app (lifespan loads BGE-M3 + reranker once) and each
test works inside an isolated notebook that is deleted afterwards.
"""

import os

from app.main import app
from conftest import needs_llm, wait_for_file_status

from app.core import config
from fastapi.testclient import TestClient

client = TestClient(app)


def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class TestNotebooks:
    def test_create_notebook(self, client, test_notebook):
        response = client.get("/api/notebooks")
        assert response.status_code == 200
        ids = [n["notebook_id"] for n in response.json()]
        assert test_notebook in ids

    def test_rename_notebook(self, client, test_notebook):
        response = client.put(
            f"/api/notebooks/{test_notebook}",
            json={"notebook_name": "renamed-by-pytest"},
        )
        assert response.status_code == 200

        notebooks = {
            n["notebook_id"]: n for n in client.get("/api/notebooks").json()
        }
        assert notebooks[test_notebook]["notebook_name"] == "renamed-by-pytest"

    def test_delete_notebook(self, client):
        created = client.post(
            "/api/notebooks", json={"notebook_name": "pytest-delete-me"}
        ).json()

        response = client.delete(f"/api/notebooks/{created['notebook_id']}")
        assert response.status_code == 200
        assert response.json() == {"message": "deleted"}

        ids = [n["notebook_id"] for n in client.get("/api/notebooks").json()]
        assert created["notebook_id"] not in ids


class TestFiles:
    def _create_file(self, client, notebook_id, name="doc.pdf", size=123):
        response = client.post(
            f"/api/notebooks/{notebook_id}/files",
            json={"file_name": name, "file_size": size},
        )
        assert response.status_code == 200
        return response.json()

    def test_create_and_list_files(self, client, test_notebook):
        created = self._create_file(client, test_notebook)

        files = client.get(f"/api/notebooks/{test_notebook}/files").json()
        by_id = {f["id"]: f for f in files}
        assert created["id"] in by_id
        assert by_id[created["id"]]["name"] == "doc.pdf"
        assert by_id[created["id"]]["status"] == "processing"

    def test_update_file_status(self, client, test_notebook):
        created = self._create_file(client, test_notebook)

        response = client.patch(
            f"/api/files/{created['id']}/status", json={"status": "ready"}
        )
        assert response.status_code == 200

        files = {
            f["id"]: f
            for f in client.get(f"/api/notebooks/{test_notebook}/files").json()
        }
        assert files[created["id"]]["status"] == "ready"

    def test_delete_file(self, client, test_notebook):
        created = self._create_file(client, test_notebook)

        response = client.delete(f"/api/files/{created['id']}")
        assert response.status_code == 200

        ids = [
            f["id"]
            for f in client.get(f"/api/notebooks/{test_notebook}/files").json()
        ]
        assert created["id"] not in ids

    def test_upload_preserves_extension(self, client, test_notebook):
        """Uploaded `.txt` must be stored with its `.txt` suffix (#7)."""
        response = client.post(
            "/api/files/upload",
            data={"notebook_id": test_notebook},
            files={"file": ("notes.txt", b"hello world", "text/plain")},
        )
        assert response.status_code == 200
        file_id = response.json()["id"]
        assert os.path.exists(
            os.path.join(config.UPLOAD_DIR, test_notebook, f"{file_id}.txt")
        )

    def test_upload_without_extension_defaults_to_pdf(
        self, client, test_notebook
    ):
        response = client.post(
            "/api/files/upload",
            data={"notebook_id": test_notebook},
            files={"file": ("noext", b"%PDF-1.4 fake", "application/pdf")},
        )
        assert response.status_code == 200
        file_id = response.json()["id"]
        assert os.path.exists(
            os.path.join(config.UPLOAD_DIR, test_notebook, f"{file_id}.pdf")
        )

    def test_process_file_reaches_terminal_status(
        self, client, test_notebook
    ):
        """End-to-end ingestion wiring: upload -> process -> ready/error."""
        uploaded = client.post(
            "/api/files/upload",
            data={"notebook_id": test_notebook},
            files={"file": ("notes.txt", b"hello world", "text/plain")},
        ).json()

        response = client.post(f"/api/files/{uploaded['id']}/process")
        assert response.status_code == 200
        assert response.json() == {"message": "processing started"}

        status = wait_for_file_status(
            client, test_notebook, uploaded["id"], timeout=180
        )
        assert status in ("ready", "error")


class TestMessages:
    def test_create_and_list_messages(self, client, test_notebook):
        created = client.post(
            f"/api/notebooks/{test_notebook}/messages",
            json={"role": "user", "text": "hello pytest"},
        )
        assert created.status_code == 200
        assert created.json()["text"] == "hello pytest"

        messages = client.get(
            f"/api/notebooks/{test_notebook}/messages"
        ).json()
        assert any(
            m["id"] == created.json()["id"] and m["role"] == "user"
            for m in messages
        )


class TestRetrieval:
    def test_retrieve_context_empty_notebook(self, client, test_notebook):
        """Real VectorRAG (BGE-M3 + reranker + pgvector) on an empty nb."""
        context = app.state.rag.retrieve_context(test_notebook, "hello world")
        assert context["query"] == "hello world"
        assert context["results"] == []


class TestPrompt:
    @needs_llm
    def test_prompt_non_stream(self, client, test_notebook):
        response = client.post(
            "/api/prompt",
            json={"prompt": "Reply with the word: ok", "notebook_id": test_notebook},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["chatbot_response"]
        assert "sources" in body

    @needs_llm
    def test_prompt_stream(self, client, test_notebook):
        response = client.post(
            "/api/prompt/stream",
            json={"prompt": "Reply with the word: ok", "notebook_id": test_notebook},
        )
        assert response.status_code == 200
        assert "data: [DONE]" in response.text
