from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
import re

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes.documents import CHANGE_SOURCE_AI, create_document_version_snapshot
from app.api.routes.chat import _get_project_or_404
from app.core.config import Settings, get_settings
from app.core.filenames import sanitize_document_filename
from app.db.session import get_db
from app.models.document import Document
from app.schemas.ai import (
    AcceptDocumentEditRequest,
    AcceptGeneratedDocumentRequest,
    GenerateDocumentRequest,
    ProposeDocumentEditRequest,
)
from app.schemas.document import DocumentRead
from app.services.context_builder import ContextBuilder
from app.services.llm import LLMProvider, stream_chat_events, get_llm_provider
from app.services.search import get_search_service

router = APIRouter(tags=["ai"])

_ALLOWED_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_ESTIMATED_CHARS_PER_TOKEN = 4
_EDIT_OUTPUT_SAFETY_MARGIN = 0.8


def _sse_event(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _derive_title_from_filename(filename: str) -> str:
    stem = Path(filename).stem.strip()
    if not stem:
        return "Untitled"
    normalized = stem.replace("_", " ").replace("-", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return " ".join(part.capitalize() for part in normalized.split()) or "Untitled"


def _normalize_markdown_filename(raw_filename: str) -> str:
    candidate = sanitize_document_filename(raw_filename)
    suffix = Path(candidate).suffix.lower()
    if not suffix:
        candidate = f"{candidate}.md"
        suffix = ".md"
    if suffix not in _ALLOWED_MARKDOWN_SUFFIXES:
        raise ValueError("Filename must end with .md or .markdown")
    return candidate


def _resolve_proposal_title_and_filename(payload: GenerateDocumentRequest) -> tuple[str, str]:
    if payload.filename is not None and payload.filename.strip():
        try:
            filename = _normalize_markdown_filename(payload.filename)
        except ValueError as err:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err)) from err
    else:
        filename = "generated-document.md"

    if payload.title is not None and payload.title.strip():
        title = payload.title.strip()
    else:
        title = _derive_title_from_filename(filename)

    return title, filename


def _build_ai_change_summary(instruction: str | None) -> str:
    if instruction is None:
        return "AI edit accepted"
    normalized = re.sub(r"\s+", " ", instruction).strip()
    if not normalized:
        return "AI edit accepted"
    if len(normalized) > 160:
        normalized = f"{normalized[:157].rstrip()}..."
    return f"AI edit accepted: {normalized}"


def _target_document_too_large_for_edit_output(target_markdown: str, edit_output_tokens: int) -> bool:
    # This preflight check estimates whether a complete revised copy of the target
    # can reasonably fit in the configured edit output budget. We reserve a safety
    # margin to account for tokenization variance and rewrite growth.
    estimated_target_tokens = max(1, len(target_markdown) // _ESTIMATED_CHARS_PER_TOKEN)
    conservative_capacity = int(edit_output_tokens * _EDIT_OUTPUT_SAFETY_MARGIN)
    return estimated_target_tokens > conservative_capacity


def get_context_builder_dependency(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ContextBuilder:
    return ContextBuilder(db=db, settings=settings, search_service=get_search_service(db))


def get_llm_provider_dependency(settings: Settings = Depends(get_settings)) -> LLMProvider:
    try:
        return get_llm_provider(settings)
    except RuntimeError as err:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(err)) from err


@router.post("/projects/{project_id}/ai/generate-document")
async def generate_document(
    project_id: int,
    payload: GenerateDocumentRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    context_builder: ContextBuilder = Depends(get_context_builder_dependency),
    llm_provider: LLMProvider = Depends(get_llm_provider_dependency),
) -> StreamingResponse:
    _get_project_or_404(project_id, db)

    selected_document_id = payload.selected_document_id
    if selected_document_id is not None:
        selected_document = db.get(Document, selected_document_id)
        if selected_document is None or selected_document.project_id != project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    instruction = payload.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Instruction cannot be empty")

    proposal_title, proposal_filename = _resolve_proposal_title_and_filename(payload)

    built_context = context_builder.build_generation(
        project_id=project_id,
        instruction=instruction,
        selected_document_id=selected_document_id,
        requested_output_filename=proposal_filename,
    )

    async def event_stream() -> AsyncIterator[str]:
        proposal_chunks: list[str] = []

        try:
            async for chunk in llm_provider.stream_chat(
                messages=built_context.messages,
                max_output_tokens=settings.ai_max_output_tokens,
            ):
                proposal_chunks.append(chunk)
                yield _sse_event({"type": "chunk", "content": chunk})

            proposal_content = "".join(proposal_chunks).strip()
            if not proposal_content:
                raise RuntimeError("AI provider returned an empty response")

            yield _sse_event(
                {
                    "type": "done",
                    "title": proposal_title,
                    "filename": proposal_filename,
                    "used_document_ids": built_context.used_document_ids,
                    "used_document_filenames": built_context.used_document_filenames,
                    "context_documents": built_context.context_documents,
                    "truncated": built_context.truncated,
                }
            )
        except Exception as err:  # pragma: no cover - defensive guard for streaming path
            yield _sse_event({"type": "error", "detail": str(err)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post(
    "/projects/{project_id}/ai/generated-document/accept",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
)
def accept_generated_document(
    project_id: int,
    payload: AcceptGeneratedDocumentRequest,
    db: Session = Depends(get_db),
) -> Document:
    _get_project_or_404(project_id, db)

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Title cannot be empty")

    try:
        filename = _normalize_markdown_filename(payload.filename)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err)) from err

    document = Document(
        project_id=project_id,
        title=title,
        filename=filename,
        markdown_content=payload.markdown_content,
        current_version=1,
    )
    db.add(document)

    try:
        db.commit()
    except IntegrityError as err:
        db.rollback()
        message = str(err.orig)
        if "uq_documents_project_filename" in message or "documents.project_id, documents.filename" in message:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Document filename must be unique within a project",
            ) from None
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to accept proposal") from None

    db.refresh(document)
    return document


@router.post("/projects/{project_id}/documents/{document_id}/ai/propose-edit")
async def propose_document_edit(
    project_id: int,
    document_id: int,
    payload: ProposeDocumentEditRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    context_builder: ContextBuilder = Depends(get_context_builder_dependency),
    llm_provider: LLMProvider = Depends(get_llm_provider_dependency),
) -> StreamingResponse:
    _get_project_or_404(project_id, db)

    target_document = db.get(Document, document_id)
    if target_document is None or target_document.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    instruction = payload.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Instruction cannot be empty")

    if _target_document_too_large_for_edit_output(
        target_markdown=target_document.markdown_content,
        edit_output_tokens=settings.ai_edit_max_output_tokens,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Target document is too large for the configured AI edit output budget. "
                "Increase AI_EDIT_MAX_OUTPUT_TOKENS or shorten the target document before requesting a full rewrite."
            ),
        )

    base_version = target_document.current_version
    built_context = context_builder.build_edit(
        project_id=project_id,
        instruction=instruction,
        target_document_id=target_document.id,
    )

    async def event_stream() -> AsyncIterator[str]:
        proposal_chunks: list[str] = []
        completion_status = "completed"

        try:
            async for event in stream_chat_events(
                provider=llm_provider,
                messages=built_context.messages,
                max_output_tokens=settings.ai_edit_max_output_tokens,
            ):
                if event.type == "chunk":
                    proposal_chunks.append(event.content)
                    yield _sse_event({"type": "chunk", "content": event.content})
                elif event.type == "complete":
                    completion_status = event.completion_status

            proposal_content = "".join(proposal_chunks).strip()
            if not proposal_content:
                raise RuntimeError("AI provider returned an empty response")

            if completion_status == "output_truncated":
                yield _sse_event(
                    {
                        "type": "error",
                        "code": "output_truncated",
                        "detail": "The proposed edit exceeded the model output limit and is incomplete.",
                    }
                )
                return

            yield _sse_event(
                {
                    "type": "done",
                    "document_id": target_document.id,
                    "base_version": base_version,
                    "used_document_ids": built_context.used_document_ids,
                    "used_document_filenames": built_context.used_document_filenames,
                    "context_documents": built_context.context_documents,
                    "truncated": built_context.truncated,
                }
            )
        except Exception as err:  # pragma: no cover - defensive guard for streaming path
            yield _sse_event({"type": "error", "code": "provider_error", "detail": str(err)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post(
    "/projects/{project_id}/documents/{document_id}/ai/accept-edit",
    response_model=DocumentRead,
)
def accept_document_edit(
    project_id: int,
    document_id: int,
    payload: AcceptDocumentEditRequest,
    db: Session = Depends(get_db),
) -> Document:
    _get_project_or_404(project_id, db)

    document = db.get(Document, document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if document.current_version != payload.base_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document changed since proposal generation. Regenerate and review the proposal.",
        )

    if payload.markdown_content == document.markdown_content:
        return document

    try:
        create_document_version_snapshot(
            db=db,
            document=document,
            change_source=CHANGE_SOURCE_AI,
            change_summary=_build_ai_change_summary(payload.instruction),
        )
        document.current_version += 1
        document.markdown_content = payload.markdown_content
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to accept edit proposal") from None
    except Exception:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to accept edit proposal") from None

    db.refresh(document)
    return document
