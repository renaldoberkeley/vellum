from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.message import Message
from app.models.project import Project
from app.schemas.chat import ConversationCreate, ConversationRead, MessageCreate, MessageRead
from app.services.context_builder import ContextBuilder
from app.services.llm import LLMProvider, get_llm_provider
from app.services.search import get_search_service

router = APIRouter(tags=["chat"])


def _get_project_or_404(project_id: int, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _get_project_conversation_or_404(project_id: int, conversation_id: int, db: Session) -> Conversation:
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.project_id == project_id)
        .first()
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


def get_context_builder_dependency(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ContextBuilder:
    return ContextBuilder(db=db, settings=settings, search_service=get_search_service(db))


def get_llm_provider_dependency(settings: Settings = Depends(get_settings)) -> LLMProvider:
    return get_llm_provider(settings)


@router.get("/projects/{project_id}/conversations", response_model=list[ConversationRead])
def list_project_conversations(project_id: int, db: Session = Depends(get_db)) -> list[Conversation]:
    _get_project_or_404(project_id, db)
    return (
        db.query(Conversation)
        .filter(Conversation.project_id == project_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .all()
    )


@router.post("/projects/{project_id}/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
def create_project_conversation(
    project_id: int,
    payload: ConversationCreate,
    db: Session = Depends(get_db),
) -> Conversation:
    _get_project_or_404(project_id, db)
    title = payload.title.strip() if payload.title is not None else None
    conversation = Conversation(project_id=project_id, title=title or None)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get(
    "/projects/{project_id}/conversations/{conversation_id}/messages",
    response_model=list[MessageRead],
)
def list_conversation_messages(
    project_id: int,
    conversation_id: int,
    db: Session = Depends(get_db),
) -> list[Message]:
    _get_project_or_404(project_id, db)
    _get_project_conversation_or_404(project_id, conversation_id, db)
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )


def _sse_event(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/projects/{project_id}/conversations/{conversation_id}/messages")
async def send_conversation_message(
    project_id: int,
    conversation_id: int,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    context_builder: ContextBuilder = Depends(get_context_builder_dependency),
    llm_provider: LLMProvider = Depends(get_llm_provider_dependency),
) -> StreamingResponse:
    _get_project_or_404(project_id, db)
    _get_project_conversation_or_404(project_id, conversation_id, db)

    selected_document_id = payload.selected_document_id
    if selected_document_id is not None:
        selected_document = db.get(Document, selected_document_id)
        if selected_document is None or selected_document.project_id != project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    history_before_current = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )

    user_content = payload.content.strip()
    if not user_content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Message content cannot be empty")

    user_message = Message(conversation_id=conversation_id, role="user", content=user_content)
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    conversation.updated_at = func.now()
    db.add(user_message)
    db.add(conversation)
    db.commit()
    db.refresh(user_message)

    built_context = context_builder.build(
        project_id=project_id,
        user_question=user_content,
        conversation_messages=history_before_current,
        selected_document_id=selected_document_id,
    )

    async def event_stream() -> AsyncIterator[str]:
        assistant_chunks: list[str] = []

        try:
            async for chunk in llm_provider.stream_chat(
                messages=built_context.messages,
                max_output_tokens=settings.ai_max_output_tokens,
            ):
                assistant_chunks.append(chunk)
                yield _sse_event({"type": "chunk", "content": chunk})

            assistant_content = "".join(assistant_chunks).strip()
            if not assistant_content:
                raise RuntimeError("AI provider returned an empty response")

            assistant_message = Message(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_content,
            )
            db.add(assistant_message)
            conversation.updated_at = func.now()
            db.add(conversation)
            db.commit()

            yield _sse_event(
                {
                    "type": "done",
                    "used_document_ids": built_context.used_document_ids,
                    "truncated": built_context.truncated,
                }
            )
        except Exception as err:  # pragma: no cover - defensive guard for streaming path
            db.rollback()
            yield _sse_event({"type": "error", "detail": str(err)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
