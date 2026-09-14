from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes.ai import get_llm_provider_dependency
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.document import Document
from app.models.document_version import DocumentVersion


class FakeLLMProvider:
    def __init__(
        self,
        chunks: list[str] | None = None,
        should_fail: bool = False,
        fail_after_chunks: int | None = None,
    ):
        self._chunks = ["# Generated\n\nDraft"] if chunks is None else chunks
        self._should_fail = should_fail
        self._fail_after_chunks = fail_after_chunks
        self.calls: list[list[dict[str, str]]] = []

    async def stream_chat(self, messages, max_output_tokens: int) -> AsyncIterator[str]:  # type: ignore[no-untyped-def]
        del max_output_tokens
        self.calls.append([{"role": message.role, "content": message.content} for message in messages])
        if self._should_fail:
            raise RuntimeError("provider failure")
        for index, chunk in enumerate(self._chunks, start=1):
            yield chunk
            if self._fail_after_chunks is not None and index >= self._fail_after_chunks:
                raise RuntimeError("provider failure after chunks")


def _parse_sse_payloads(body: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for raw_event in body.split("\n\n"):
        for line in raw_event.split("\n"):
            stripped = line.strip()
            if stripped.startswith("data:"):
                payloads.append(json.loads(stripped[5:].strip()))
    return payloads


def _create_project(client: TestClient, name: str = "AI Project") -> int:
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def _create_document(
    client: TestClient,
    project_id: int,
    title: str,
    filename: str,
    markdown_content: str,
) -> dict[str, object]:
    response = client.post(
        f"/api/projects/{project_id}/documents",
        json={"title": title, "filename": filename, "markdown_content": markdown_content},
    )
    assert response.status_code == 201
    return response.json()


def _count_project_documents(db: Session, project_id: int) -> int:
    count = db.scalar(select(func.count()).select_from(Document).where(Document.project_id == project_id))
    return int(count or 0)


def _count_document_versions(db: Session, document_id: int) -> int:
    count = db.scalar(select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == document_id))
    return int(count or 0)


def _create_test_env(fake_provider: FakeLLMProvider) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_llm_provider_dependency] = lambda: fake_provider

    with TestClient(app) as test_client:
        yield test_client, testing_session_local

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_generate_streams_and_does_not_create_document_before_acceptance() -> None:
    provider = FakeLLMProvider(chunks=["# Proposal", "\n\nBody"])
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Vision", "1. vision.md", "vision context")

        with session_factory() as db:
            assert _count_project_documents(db, project_id) == 1

        response = client.post(
            f"/api/projects/{project_id}/ai/generate-document",
            json={"instruction": "Create release-plan.md based on vision.md", "filename": "release-plan.md"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "chunk" for event in events)
        done_event = next((event for event in events if event.get("type") == "done"), None)
        assert done_event is not None
        assert done_event.get("filename") == "release-plan.md"

        with session_factory() as db:
            assert _count_project_documents(db, project_id) == 1


def test_generate_uses_project_documents_in_context() -> None:
    provider = FakeLLMProvider(chunks=["# Draft"])
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Vision", "1. vision.md", "VISION SOURCE")

        response = client.post(
            f"/api/projects/{project_id}/ai/generate-document",
            json={"instruction": "Create roadmap.md from vision.md"},
        )
        assert response.status_code == 200
        assert provider.calls
        user_payload = provider.calls[-1][-1]["content"]
        assert "Filename: 1. vision.md" in user_payload
        assert "VISION SOURCE" in user_payload


def test_generate_explicit_references_and_prefixed_aliases_are_prioritized() -> None:
    provider = FakeLLMProvider(chunks=["# Draft"])
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Product Requirements", "2. product-requirements.md", "REQ")
        _create_document(client, project_id, "Technical Architecture", "4. technical-architecture.md", "ARCH")

        response = client.post(
            f"/api/projects/{project_id}/ai/generate-document",
            json={
                "instruction": "Create deployment-plan.md based on product-requirements.md and technical-architecture.md.",
                "filename": "deployment-plan.md",
            },
        )
        assert response.status_code == 200

        done_event = next(
            event
            for event in _parse_sse_payloads(response.text)
            if event.get("type") == "done"
        )
        used_filenames = done_event.get("used_document_filenames")
        assert isinstance(used_filenames, list)
        assert "2. product-requirements.md" in used_filenames
        assert "4. technical-architecture.md" in used_filenames

        context_documents = done_event.get("context_documents")
        assert isinstance(context_documents, list)
        reason_by_filename = {
            item["filename"]: item["reason"]
            for item in context_documents
            if isinstance(item, dict)
        }
        assert reason_by_filename.get("2. product-requirements.md") == "explicit_reference"
        assert reason_by_filename.get("4. technical-architecture.md") == "explicit_reference"


def test_generate_two_explicit_docs_survive_context_pressure() -> None:
    provider = FakeLLMProvider(chunks=["# Draft"])
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Product Requirements", "2. product-requirements.md", "REQ " * 3500)
        _create_document(client, project_id, "Technical Architecture", "4. technical-architecture.md", "ARCH " * 3500)
        _create_document(client, project_id, "Noise", "zzz-injection.md", "architecture architecture " * 3500)

        response = client.post(
            f"/api/projects/{project_id}/ai/generate-document",
            json={
                "instruction": "Compare product-requirements.md against technical-architecture.md and create a plan.",
                "filename": "phase-5-plan.md",
            },
        )
        assert response.status_code == 200

        done_event = next(
            event
            for event in _parse_sse_payloads(response.text)
            if event.get("type") == "done"
        )
        used_filenames = done_event.get("used_document_filenames")
        assert isinstance(used_filenames, list)
        assert "2. product-requirements.md" in used_filenames
        assert "4. technical-architecture.md" in used_filenames
        assert done_event.get("truncated") is True


def test_generate_excludes_requested_output_filename_from_explicit_reference_resolution() -> None:
    provider = FakeLLMProvider(chunks=["# Draft"])
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Existing Plan", "phase-5-plan.md", "old plan")
        _create_document(client, project_id, "Product Requirements", "2. product-requirements.md", "REQ " * 3500)
        _create_document(client, project_id, "Technical Architecture", "4. technical-architecture.md", "ARCH " * 3500)

        response = client.post(
            f"/api/projects/{project_id}/ai/generate-document",
            json={
                "instruction": "Create phase-5-plan.md based on product-requirements.md and technical-architecture.md.",
                "filename": "phase-5-plan.md",
            },
        )
        assert response.status_code == 200

        done_event = next(
            event
            for event in _parse_sse_payloads(response.text)
            if event.get("type") == "done"
        )
        used_filenames = done_event.get("used_document_filenames")
        assert isinstance(used_filenames, list)
        assert "2. product-requirements.md" in used_filenames
        assert "4. technical-architecture.md" in used_filenames
        assert "phase-5-plan.md" not in used_filenames

        context_documents = done_event.get("context_documents")
        assert isinstance(context_documents, list)
        reason_by_filename = {
            item["filename"]: item["reason"]
            for item in context_documents
            if isinstance(item, dict)
        }
        assert reason_by_filename.get("2. product-requirements.md") == "explicit_reference"
        assert reason_by_filename.get("4. technical-architecture.md") == "explicit_reference"


def test_generate_keeps_selected_source_document_when_filename_matches_output() -> None:
    provider = FakeLLMProvider(chunks=["# Draft"])
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        existing_plan = _create_document(client, project_id, "Existing Plan", "phase-5-plan.md", "old plan")
        _create_document(client, project_id, "Product Requirements", "2. product-requirements.md", "REQ")

        response = client.post(
            f"/api/projects/{project_id}/ai/generate-document",
            json={
                "instruction": "Create phase-5-plan.md based on product-requirements.md.",
                "filename": "phase-5-plan.md",
                "selected_document_id": int(existing_plan["id"]),
            },
        )
        assert response.status_code == 200

        done_event = next(
            event
            for event in _parse_sse_payloads(response.text)
            if event.get("type") == "done"
        )
        context_documents = done_event.get("context_documents")
        assert isinstance(context_documents, list)
        reason_by_filename = {
            item["filename"]: item["reason"]
            for item in context_documents
            if isinstance(item, dict)
        }
        assert reason_by_filename.get("phase-5-plan.md") == "selected"


def test_generate_with_cross_project_selected_document_returns_404() -> None:
    provider = FakeLLMProvider(chunks=["# Draft"])
    for client, _ in _create_test_env(provider):
        project_a = _create_project(client, "A")
        project_b = _create_project(client, "B")
        foreign = _create_document(client, project_b, "Foreign", "foreign.md", "secret")

        response = client.post(
            f"/api/projects/{project_a}/ai/generate-document",
            json={"instruction": "Create summary.md", "selected_document_id": int(foreign["id"])},
        )
        assert response.status_code == 404


def test_generate_project_isolation_prevents_cross_project_leakage() -> None:
    provider = FakeLLMProvider(chunks=["# Draft"])
    for client, _ in _create_test_env(provider):
        project_a = _create_project(client, "A")
        project_b = _create_project(client, "B")
        _create_document(client, project_a, "Vision", "1. vision.md", "alpha-only")
        _create_document(client, project_b, "Secret", "secret.md", "beta-secret")

        response = client.post(
            f"/api/projects/{project_a}/ai/generate-document",
            json={"instruction": "Create summary.md based on vision.md"},
        )
        assert response.status_code == 200
        assert provider.calls
        user_payload = provider.calls[-1][-1]["content"]
        assert "alpha-only" in user_payload
        assert "beta-secret" not in user_payload


def test_generate_provider_failure_before_chunks_creates_no_document() -> None:
    provider = FakeLLMProvider(should_fail=True)
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Vision", "vision.md", "context")

        response = client.post(
            f"/api/projects/{project_id}/ai/generate-document",
            json={"instruction": "Create x.md"},
        )
        assert response.status_code == 200
        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "error" for event in events)

        with session_factory() as db:
            assert _count_project_documents(db, project_id) == 1


def test_generate_provider_failure_after_partial_chunks_creates_no_document() -> None:
    provider = FakeLLMProvider(chunks=["# partial"], fail_after_chunks=1)
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Vision", "vision.md", "context")

        response = client.post(
            f"/api/projects/{project_id}/ai/generate-document",
            json={"instruction": "Create x.md"},
        )
        assert response.status_code == 200
        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "chunk" for event in events)
        assert any(event.get("type") == "error" for event in events)

        with session_factory() as db:
            assert _count_project_documents(db, project_id) == 1


def test_generate_empty_provider_output_creates_no_document() -> None:
    provider = FakeLLMProvider(chunks=[])
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Vision", "vision.md", "context")

        response = client.post(
            f"/api/projects/{project_id}/ai/generate-document",
            json={"instruction": "Create x.md"},
        )
        assert response.status_code == 200
        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "error" for event in events)

        with session_factory() as db:
            assert _count_project_documents(db, project_id) == 1


def test_accept_generated_document_creates_version_one_without_history_row() -> None:
    provider = FakeLLMProvider()
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)

        response = client.post(
            f"/api/projects/{project_id}/ai/generated-document/accept",
            json={
                "title": "Phase 5 Plan",
                "filename": "phase-5-plan.md",
                "markdown_content": "# Phase 5\n\nDraft plan",
            },
        )
        assert response.status_code == 201
        document = response.json()
        assert document["current_version"] == 1
        assert document["markdown_content"] == "# Phase 5\n\nDraft plan"

        with session_factory() as db:
            assert _count_document_versions(db, int(document["id"])) == 0


def test_accept_generated_document_duplicate_filename_returns_409() -> None:
    provider = FakeLLMProvider()
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Existing", "phase-5-plan.md", "existing")

        response = client.post(
            f"/api/projects/{project_id}/ai/generated-document/accept",
            json={
                "title": "Phase 5 Plan",
                "filename": "phase-5-plan.md",
                "markdown_content": "# New",
            },
        )
        assert response.status_code == 409


def test_accept_generated_document_filename_sanitized_and_markdown_validated() -> None:
    provider = FakeLLMProvider()
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)

        valid_response = client.post(
            f"/api/projects/{project_id}/ai/generated-document/accept",
            json={
                "title": "Sanitized",
                "filename": " nested/path/phase-5-plan ",
                "markdown_content": "# Body",
            },
        )
        assert valid_response.status_code == 201
        assert valid_response.json()["filename"] == "phase-5-plan.md"

        invalid_response = client.post(
            f"/api/projects/{project_id}/ai/generated-document/accept",
            json={
                "title": "Bad",
                "filename": "plan.txt",
                "markdown_content": "# Body",
            },
        )
        assert invalid_response.status_code == 422


def test_accept_generated_document_project_scoping_and_exact_content() -> None:
    provider = FakeLLMProvider()
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)

        content = "# Proposed\n\nLine A\nLine B"
        response = client.post(
            f"/api/projects/{project_id}/ai/generated-document/accept",
            json={
                "title": "Proposed",
                "filename": "proposed.md",
                "markdown_content": content,
            },
        )
        assert response.status_code == 201
        assert response.json()["project_id"] == project_id
        assert response.json()["markdown_content"] == content

        missing_project_response = client.post(
            "/api/projects/999999/ai/generated-document/accept",
            json={"title": "X", "filename": "x.md", "markdown_content": "# X"},
        )
        assert missing_project_response.status_code == 404
