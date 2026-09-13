from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.message import Message
from app.api.routes.chat import get_llm_provider_dependency
from app.services.context_builder import ContextBuilder
from app.services.search import PostgresLexicalSearchService
from app.core.config import Settings


class FakeLLMProvider:
    def __init__(
        self,
        chunks: list[str] | None = None,
        should_fail: bool = False,
        fail_after_chunks: int | None = None,
    ):
        self._chunks = ["Hello", " world"] if chunks is None else chunks
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


def _create_project(client: TestClient, name: str = "Chat Project") -> int:
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


def _create_conversation(client: TestClient, project_id: int, title: str | None = None) -> int:
    response = client.post(f"/api/projects/{project_id}/conversations", json={"title": title})
    assert response.status_code == 201
    return response.json()["id"]


def _chat_settings(context_chars: int = 3000) -> Settings:
    return Settings(
        ai_max_context_chars=context_chars,
        ai_max_history_messages=8,
        ai_max_output_tokens=300,
    )


def _build_context_for_test(db: Session, project_id: int, question: str, selected_document_id: int | None = None):
    builder = ContextBuilder(
        db=db,
        settings=_chat_settings(context_chars=600),
        search_service=PostgresLexicalSearchService(db),
    )
    return builder.build(
        project_id=project_id,
        user_question=question,
        conversation_messages=[],
        selected_document_id=selected_document_id,
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


def test_conversation_creation_scoped_to_project() -> None:
    provider = FakeLLMProvider()
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client, "A")
        other_project_id = _create_project(client, "B")

        conversation_id = _create_conversation(client, project_id, "Planning")
        assert conversation_id > 0

        list_response = client.get(f"/api/projects/{project_id}/conversations")
        assert list_response.status_code == 200
        assert len(list_response.json()) == 1

        other_list_response = client.get(f"/api/projects/{other_project_id}/conversations")
        assert other_list_response.status_code == 200
        assert other_list_response.json() == []


def test_conversation_message_ownership_checks() -> None:
    provider = FakeLLMProvider()
    for client, _ in _create_test_env(provider):
        project_a = _create_project(client, "A")
        project_b = _create_project(client, "B")
        conversation_id = _create_conversation(client, project_a)

        list_wrong = client.get(f"/api/projects/{project_b}/conversations/{conversation_id}/messages")
        assert list_wrong.status_code == 404

        post_wrong = client.post(
            f"/api/projects/{project_b}/conversations/{conversation_id}/messages",
            json={"content": "hi"},
        )
        assert post_wrong.status_code == 404


def test_context_builder_includes_project_documents_and_isolation() -> None:
    provider = FakeLLMProvider()
    for client, session_factory in _create_test_env(provider):
        project_a = _create_project(client, "A")
        project_b = _create_project(client, "B")

        _create_document(client, project_a, "Vision", "vision.md", "alpha context")
        _create_document(client, project_b, "Other", "other.md", "beta private")

        with session_factory() as db:
            context = _build_context_for_test(db, project_a, "What is the vision?")

        user_payload = context.messages[1].content
        assert "vision.md" in user_payload
        assert "alpha context" in user_payload
        assert "other.md" not in user_payload
        assert "beta private" not in user_payload


def test_selected_document_preference_when_budget_limited() -> None:
    provider = FakeLLMProvider()
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        selected = _create_document(client, project_id, "Selected", "selected.md", "selected important details")
        _create_document(client, project_id, "General", "general.md", "general content " * 80)

        with session_factory() as db:
            context = _build_context_for_test(
                db,
                project_id,
                "selected",
                selected_document_id=int(selected["id"]),
            )

        user_payload = context.messages[1].content
        assert "selected.md" in user_payload


def test_send_message_with_valid_selected_document_succeeds() -> None:
    provider = FakeLLMProvider(chunks=["ok"])
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        selected = _create_document(client, project_id, "Selected", "selected.md", "selected context")
        conversation_id = _create_conversation(client, project_id)

        response = client.post(
            f"/api/projects/{project_id}/conversations/{conversation_id}/messages",
            json={"content": "Use selected", "selected_document_id": int(selected["id"])},
        )
        assert response.status_code == 200
        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "done" for event in events)


def test_send_message_with_nonexistent_selected_document_returns_404() -> None:
    provider = FakeLLMProvider(chunks=["ok"])
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        conversation_id = _create_conversation(client, project_id)

        response = client.post(
            f"/api/projects/{project_id}/conversations/{conversation_id}/messages",
            json={"content": "Use selected", "selected_document_id": 999999},
        )
        assert response.status_code == 404


def test_send_message_with_cross_project_selected_document_returns_404() -> None:
    provider = FakeLLMProvider(chunks=["ok"])
    for client, _ in _create_test_env(provider):
        project_a = _create_project(client, "A")
        project_b = _create_project(client, "B")
        foreign_doc = _create_document(client, project_b, "Foreign", "foreign.md", "foreign")
        conversation_id = _create_conversation(client, project_a)

        response = client.post(
            f"/api/projects/{project_a}/conversations/{conversation_id}/messages",
            json={"content": "Use selected", "selected_document_id": int(foreign_doc["id"])},
        )
        assert response.status_code == 404


def test_current_question_appears_once_in_provider_context() -> None:
    provider = FakeLLMProvider(chunks=["ok"])
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Vision", "vision.md", "Project content")
        conversation_id = _create_conversation(client, project_id)
        unique_question = "UNIQUE_QUESTION_4D2A7F"

        response = client.post(
            f"/api/projects/{project_id}/conversations/{conversation_id}/messages",
            json={"content": unique_question},
        )
        assert response.status_code == 200
        assert provider.calls

        user_payload = provider.calls[-1][-1]["content"]
        assert user_payload.count(unique_question) == 1
        assert f"USER: {unique_question}" not in user_payload


def test_provider_failure_after_chunk_does_not_persist_assistant_message() -> None:
    provider = FakeLLMProvider(chunks=["partial"], fail_after_chunks=1)
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Vision", "vision.md", "Project content")
        conversation_id = _create_conversation(client, project_id)

        response = client.post(
            f"/api/projects/{project_id}/conversations/{conversation_id}/messages",
            json={"content": "Summarize"},
        )
        assert response.status_code == 200
        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "chunk" for event in events)
        assert any(event.get("type") == "error" for event in events)

        messages_response = client.get(
            f"/api/projects/{project_id}/conversations/{conversation_id}/messages"
        )
        messages = messages_response.json()
        assert len(messages) == 1
        assert messages[0]["role"] == "user"


def test_empty_provider_response_does_not_persist_assistant_message() -> None:
    provider = FakeLLMProvider(chunks=[])
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Vision", "vision.md", "Project content")
        conversation_id = _create_conversation(client, project_id)

        response = client.post(
            f"/api/projects/{project_id}/conversations/{conversation_id}/messages",
            json={"content": "Summarize"},
        )
        assert response.status_code == 200
        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "error" for event in events)

        messages_response = client.get(
            f"/api/projects/{project_id}/conversations/{conversation_id}/messages"
        )
        messages = messages_response.json()
        assert len(messages) == 1
        assert messages[0]["role"] == "user"


def test_context_builder_sets_truncated_true_when_documents_omitted() -> None:
    provider = FakeLLMProvider()
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Doc 1", "one.md", "A" * 1800)
        _create_document(client, project_id, "Doc 2", "two.md", "B" * 1800)

        with session_factory() as db:
            context = _build_context_for_test(db, project_id, "Question")

        assert context.truncated is True


def test_streaming_message_persists_user_and_final_assistant_only() -> None:
    provider = FakeLLMProvider(chunks=["Part 1", " and part 2"])
    for client, session_factory in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Vision", "vision.md", "Project content")
        conversation_id = _create_conversation(client, project_id)

        response = client.post(
            f"/api/projects/{project_id}/conversations/{conversation_id}/messages",
            json={"content": "Summarize", "selected_document_id": None},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "chunk" for event in events)
        assert any(event.get("type") == "done" for event in events)

        messages_response = client.get(
            f"/api/projects/{project_id}/conversations/{conversation_id}/messages"
        )
        assert messages_response.status_code == 200
        messages = messages_response.json()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Part 1 and part 2"

        with session_factory() as db:
            db_messages = db.query(Message).filter(Message.conversation_id == conversation_id).all()
            assert len(db_messages) == 2


def test_provider_failure_does_not_persist_assistant_message() -> None:
    provider = FakeLLMProvider(should_fail=True)
    for client, _ in _create_test_env(provider):
        project_id = _create_project(client)
        _create_document(client, project_id, "Vision", "vision.md", "Project content")
        conversation_id = _create_conversation(client, project_id)

        response = client.post(
            f"/api/projects/{project_id}/conversations/{conversation_id}/messages",
            json={"content": "Summarize"},
        )
        assert response.status_code == 200
        events = _parse_sse_payloads(response.text)
        assert any(event.get("type") == "error" for event in events)

        messages_response = client.get(
            f"/api/projects/{project_id}/conversations/{conversation_id}/messages"
        )
        messages = messages_response.json()
        assert len(messages) == 1
        assert messages[0]["role"] == "user"


def test_streaming_context_does_not_leak_cross_project_documents() -> None:
    provider = FakeLLMProvider(chunks=["ok"])
    for client, _ in _create_test_env(provider):
        project_a = _create_project(client, "A")
        project_b = _create_project(client, "B")
        _create_document(client, project_a, "A Doc", "a.md", "alpha-only")
        _create_document(client, project_b, "B Doc", "b.md", "beta-secret")
        conversation_id = _create_conversation(client, project_a)

        response = client.post(
            f"/api/projects/{project_a}/conversations/{conversation_id}/messages",
            json={"content": "what exists"},
        )
        assert response.status_code == 200

        assert provider.calls, "Provider should have been called"
        latest_call = provider.calls[-1]
        user_payload = latest_call[-1]["content"]
        assert "alpha-only" in user_payload
        assert "beta-secret" not in user_payload
