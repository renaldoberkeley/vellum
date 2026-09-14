from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes.ai import (
    _target_document_too_large_for_edit_output,
    get_llm_provider_dependency,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.services.llm import LLMStreamEvent


class FakeLLMProvider:
    def __init__(
        self,
        chunks: list[str] | None = None,
        should_fail: bool = False,
        fail_after_chunks: int | None = None,
        truncate_on_completion: bool = False,
    ):
        self._chunks = ["# Revised\n\nUpdated body"] if chunks is None else chunks
        self._should_fail = should_fail
        self._fail_after_chunks = fail_after_chunks
        self._truncate_on_completion = truncate_on_completion
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

    async def stream_chat_events(
        self,
        messages,
        max_output_tokens: int,
    ) -> AsyncIterator[LLMStreamEvent]:  # type: ignore[no-untyped-def]
        del max_output_tokens
        self.calls.append([{"role": message.role, "content": message.content} for message in messages])
        if self._should_fail:
            raise RuntimeError("provider failure")
        for index, chunk in enumerate(self._chunks, start=1):
            yield LLMStreamEvent(type="chunk", content=chunk)
            if self._fail_after_chunks is not None and index >= self._fail_after_chunks:
                raise RuntimeError("provider failure after chunks")
        yield LLMStreamEvent(
            type="complete",
            completion_status="output_truncated" if self._truncate_on_completion else "completed",
        )


def _parse_sse_payloads(body: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for raw_event in body.split("\n\n"):
        for line in raw_event.split("\n"):
            stripped = line.strip()
            if stripped.startswith("data:"):
                payloads.append(json.loads(stripped[5:].strip()))
    return payloads


def _create_project(client: TestClient, name: str = "AI Edit Project") -> int:
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


def _get_document(db: Session, document_id: int) -> Document:
    document = db.get(Document, document_id)
    assert document is not None
    return document


def _count_document_versions(db: Session, document_id: int) -> int:
    count = db.scalar(select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == document_id))
    return int(count or 0)


def _list_document_versions(db: Session, document_id: int) -> list[DocumentVersion]:
    return (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.asc())
        .all()
    )


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


def test_propose_edit_streams_without_modifying_canonical_document() -> None:
    provider = FakeLLMProvider(chunks=["# Updated", "\n\nBody"])
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "4. technical-architecture.md", "OLD TARGET")
        _create_document(client, project_id, "Requirements", "2. product-requirements.md", "REQ")

        with session_factory() as db:
            document_before = _get_document(db, int(target["id"]))
            assert document_before.current_version == 1
            assert document_before.markdown_content == "OLD TARGET"
            assert _count_document_versions(db, int(target["id"])) == 0

        response = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/propose-edit",
            json={"instruction": "Update technical-architecture.md based on product-requirements.md."},
        )
        assert response.status_code == 200

        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "chunk" for event in events)
        done_event = next(event for event in events if event.get("type") == "done")
        assert done_event.get("document_id") == int(target["id"])
        assert done_event.get("base_version") == 1

        context_documents = done_event.get("context_documents")
        assert isinstance(context_documents, list)
        reason_by_filename = {
            item["filename"]: item["reason"]
            for item in context_documents
            if isinstance(item, dict)
        }
        assert reason_by_filename.get("4. technical-architecture.md") == "selected"
        assert reason_by_filename.get("2. product-requirements.md") == "explicit_reference"

        with session_factory() as db:
            document_after = _get_document(db, int(target["id"]))
            assert document_after.current_version == 1
            assert document_after.markdown_content == "OLD TARGET"
            assert _count_document_versions(db, int(target["id"])) == 0


def test_propose_edit_target_and_explicit_reference_survive_context_pressure() -> None:
    provider = FakeLLMProvider(chunks=["# Draft"])
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "4. technical-architecture.md", "ARCH " * 1500)
        _create_document(client, project_id, "Requirements", "2. product-requirements.md", "REQ " * 1500)
        _create_document(client, project_id, "Noise", "noise.md", "ARCH REQ " * 1500)

        response = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/propose-edit",
            json={"instruction": "Update technical-architecture.md based on product-requirements.md."},
        )
        assert response.status_code == 200

        done_event = next(event for event in _parse_sse_payloads(response.text) if event.get("type") == "done")
        used_filenames = done_event.get("used_document_filenames")
        assert isinstance(used_filenames, list)
        assert "4. technical-architecture.md" in used_filenames
        assert "2. product-requirements.md" in used_filenames
        assert done_event.get("truncated") is True


def test_propose_edit_explicit_references_resolve_prefixed_aliases() -> None:
    provider = FakeLLMProvider(chunks=["# Draft"])
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "4. technical-architecture.md", "ARCH")
        _create_document(client, project_id, "Requirements", "2. product-requirements.md", "REQ")

        response = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/propose-edit",
            json={"instruction": "Update technical architecture using product requirements."},
        )
        assert response.status_code == 200

        done_event = next(event for event in _parse_sse_payloads(response.text) if event.get("type") == "done")
        context_documents = done_event.get("context_documents")
        assert isinstance(context_documents, list)
        reason_by_filename = {
            item["filename"]: item["reason"]
            for item in context_documents
            if isinstance(item, dict)
        }
        assert reason_by_filename.get("4. technical-architecture.md") == "selected"
        assert reason_by_filename.get("2. product-requirements.md") == "explicit_reference"


def test_propose_edit_has_no_cross_project_context_leakage() -> None:
    provider = FakeLLMProvider(chunks=["# Draft"])
    for client, _ in _create_test_env(provider):
        project_a = _create_project(client, "A")
        project_b = _create_project(client, "B")
        target = _create_document(client, project_a, "Architecture", "4. technical-architecture.md", "ARCH A")
        _create_document(client, project_b, "Secret", "secret.md", "SECRET B")

        response = client.post(
            f"/api/projects/{project_a}/documents/{int(target['id'])}/ai/propose-edit",
            json={"instruction": "Update technical-architecture.md"},
        )
        assert response.status_code == 200

        assert provider.calls
        user_payload = provider.calls[-1][-1]["content"]
        assert "ARCH A" in user_payload
        assert "SECRET B" not in user_payload


def test_propose_edit_provider_failure_before_chunks_creates_no_change() -> None:
    provider = FakeLLMProvider(should_fail=True)
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "technical-architecture.md", "ORIGINAL")

        response = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/propose-edit",
            json={"instruction": "Update technical-architecture.md"},
        )
        assert response.status_code == 200
        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "error" for event in events)

        with session_factory() as db:
            document = _get_document(db, int(target["id"]))
            assert document.current_version == 1
            assert document.markdown_content == "ORIGINAL"
            assert _count_document_versions(db, int(target["id"])) == 0


def test_propose_edit_output_truncation_emits_error_without_done_and_no_changes() -> None:
    provider = FakeLLMProvider(chunks=["# partial", "\n\nmore"], truncate_on_completion=True)
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "technical-architecture.md", "ORIGINAL")

        response = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/propose-edit",
            json={"instruction": "Update technical-architecture.md"},
        )
        assert response.status_code == 200

        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "chunk" for event in events)
        assert not any(event.get("type") == "done" for event in events)
        error_event = next(event for event in events if event.get("type") == "error")
        assert error_event.get("code") == "output_truncated"

        with session_factory() as db:
            document = _get_document(db, int(target["id"]))
            assert document.current_version == 1
            assert document.markdown_content == "ORIGINAL"
            assert _count_document_versions(db, int(target["id"])) == 0


def test_propose_edit_rejects_oversized_target_for_output_budget() -> None:
    provider = FakeLLMProvider(chunks=["# never reached"])
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "technical-architecture.md", "X" * 26000)

        response = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/propose-edit",
            json={"instruction": "Update technical-architecture.md"},
        )
        assert response.status_code == 422
        assert "too large" in response.json()["detail"].lower()


def test_preflight_guard_allows_when_estimate_fits_conservative_capacity() -> None:
    assert not _target_document_too_large_for_edit_output(
        target_markdown="X" * 18286,
        edit_output_tokens=7500,
    )


def test_preflight_guard_rejects_when_estimate_exceeds_conservative_capacity() -> None:
    assert _target_document_too_large_for_edit_output(
        target_markdown="X" * 26000,
        edit_output_tokens=7500,
    )


def test_propose_edit_provider_failure_after_partial_chunks_creates_no_change() -> None:
    provider = FakeLLMProvider(chunks=["# partial"], fail_after_chunks=1)
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "technical-architecture.md", "ORIGINAL")

        response = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/propose-edit",
            json={"instruction": "Update technical-architecture.md"},
        )
        assert response.status_code == 200
        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "chunk" for event in events)
        assert any(event.get("type") == "error" for event in events)

        with session_factory() as db:
            document = _get_document(db, int(target["id"]))
            assert document.current_version == 1
            assert document.markdown_content == "ORIGINAL"
            assert _count_document_versions(db, int(target["id"])) == 0


def test_propose_edit_empty_provider_response_creates_no_change() -> None:
    provider = FakeLLMProvider(chunks=[])
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "technical-architecture.md", "ORIGINAL")

        response = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/propose-edit",
            json={"instruction": "Update technical-architecture.md"},
        )
        assert response.status_code == 200
        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "error" for event in events)

        with session_factory() as db:
            document = _get_document(db, int(target["id"]))
            assert document.current_version == 1
            assert document.markdown_content == "ORIGINAL"
            assert _count_document_versions(db, int(target["id"])) == 0


def test_accept_edit_updates_document_and_creates_ai_snapshot() -> None:
    provider = FakeLLMProvider()
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "technical-architecture.md", "OLD")

        response = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/accept-edit",
            json={
                "base_version": 1,
                "markdown_content": "NEW",
                "instruction": "Update architecture to reflect retrieval strategy.",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["current_version"] == 2
        assert payload["markdown_content"] == "NEW"

        with session_factory() as db:
            document = _get_document(db, int(target["id"]))
            assert document.current_version == 2
            assert document.markdown_content == "NEW"
            versions = _list_document_versions(db, int(target["id"]))
            assert len(versions) == 1
            assert versions[0].version_number == 1
            assert versions[0].markdown_content == "OLD"
            assert versions[0].change_source == "ai"
            assert versions[0].change_summary is not None
            assert versions[0].change_summary.startswith("AI edit accepted:")


def test_accept_edit_stale_base_version_returns_409_without_modification() -> None:
    provider = FakeLLMProvider()
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "technical-architecture.md", "v1")

        manual_update = client.patch(
            f"/api/projects/{project_id}/documents/{int(target['id'])}",
            json={"markdown_content": "v2"},
        )
        assert manual_update.status_code == 200

        stale_accept = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/accept-edit",
            json={
                "base_version": 1,
                "markdown_content": "stale proposal",
                "instruction": "Update architecture",
            },
        )
        assert stale_accept.status_code == 409

        with session_factory() as db:
            document = _get_document(db, int(target["id"]))
            assert document.current_version == 2
            assert document.markdown_content == "v2"
            versions = _list_document_versions(db, int(target["id"]))
            assert len(versions) == 1
            assert versions[0].change_source == "manual"


def test_accept_edit_noop_does_not_create_new_version() -> None:
    provider = FakeLLMProvider()
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "technical-architecture.md", "same")

        response = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/accept-edit",
            json={"base_version": 1, "markdown_content": "same", "instruction": "No-op"},
        )
        assert response.status_code == 200
        assert response.json()["current_version"] == 1

        with session_factory() as db:
            document = _get_document(db, int(target["id"]))
            assert document.current_version == 1
            assert document.markdown_content == "same"
            assert _count_document_versions(db, int(target["id"])) == 0


def test_accept_edit_scoping_checks_project_and_document() -> None:
    provider = FakeLLMProvider()
    for client, _ in _create_test_env(provider):
        project_a = _create_project(client, "A")
        project_b = _create_project(client, "B")
        target = _create_document(client, project_b, "Architecture", "technical-architecture.md", "body")

        missing_project_response = client.post(
            "/api/projects/999999/documents/1/ai/accept-edit",
            json={"base_version": 1, "markdown_content": "x", "instruction": "Update"},
        )
        assert missing_project_response.status_code == 404

        cross_project_response = client.post(
            f"/api/projects/{project_a}/documents/{int(target['id'])}/ai/accept-edit",
            json={"base_version": 1, "markdown_content": "x", "instruction": "Update"},
        )
        assert cross_project_response.status_code == 404


def test_accept_edit_is_atomic_when_snapshot_fails() -> None:
    provider = FakeLLMProvider()
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "technical-architecture.md", "ORIGINAL")

        with patch("app.api.routes.ai.create_document_version_snapshot", side_effect=RuntimeError("boom")):
            response = client.post(
                f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/accept-edit",
                json={
                    "base_version": 1,
                    "markdown_content": "SHOULD-NOT-PERSIST",
                    "instruction": "Update architecture",
                },
            )
        assert response.status_code == 500

        with session_factory() as db:
            document = _get_document(db, int(target["id"]))
            assert document.current_version == 1
            assert document.markdown_content == "ORIGINAL"
            assert _count_document_versions(db, int(target["id"])) == 0


def test_version_history_manual_edit_and_restore_work_after_ai_edit() -> None:
    provider = FakeLLMProvider()
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        target = _create_document(client, project_id, "Architecture", "technical-architecture.md", "v1")

        ai_accept = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/ai/accept-edit",
            json={
                "base_version": 1,
                "markdown_content": "v2-ai",
                "instruction": "Update architecture",
            },
        )
        assert ai_accept.status_code == 200
        assert ai_accept.json()["current_version"] == 2

        manual_update = client.patch(
            f"/api/projects/{project_id}/documents/{int(target['id'])}",
            json={"markdown_content": "v3-manual"},
        )
        assert manual_update.status_code == 200
        assert manual_update.json()["current_version"] == 3

        with session_factory() as db:
            versions_before_restore = _list_document_versions(db, int(target["id"]))
            assert [version.version_number for version in versions_before_restore] == [1, 2]
            assert versions_before_restore[0].change_source == "ai"
            assert versions_before_restore[1].change_source == "manual"
            restore_source_version_id = versions_before_restore[0].id

        restore_response = client.post(
            f"/api/projects/{project_id}/documents/{int(target['id'])}/versions/{restore_source_version_id}/restore",
        )
        assert restore_response.status_code == 200
        assert restore_response.json()["current_version"] == 4
        assert restore_response.json()["markdown_content"] == "v1"

        with session_factory() as db:
            document = _get_document(db, int(target["id"]))
            assert document.current_version == 4
            assert document.markdown_content == "v1"
            versions_after_restore = _list_document_versions(db, int(target["id"]))
            assert [version.version_number for version in versions_after_restore] == [1, 2, 3]
            assert versions_after_restore[0].change_source == "ai"
            assert versions_after_restore[1].change_source == "manual"
            assert versions_after_restore[2].change_source == "restore"
