from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.filenames import sanitize_document_filename
from app.db.session import get_db
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.project import Project
from app.schemas.document import DocumentCreate, DocumentRead, DocumentUpdate
from app.schemas.document_version import DocumentVersionListItem, DocumentVersionRead
from app.schemas.import_export import MarkdownImportFailure, MarkdownImportResponse

router = APIRouter(tags=["documents"])

CHANGE_SOURCE_MANUAL = "manual"
CHANGE_SOURCE_AI = "ai"
CHANGE_SOURCE_RESTORE = "restore"
CHANGE_SOURCE_IMPORT = "import"

MAX_IMPORT_FILE_SIZE_BYTES = 1_000_000
ALLOWED_MARKDOWN_SUFFIXES = {".md", ".markdown"}
ALLOWED_CONTENT_TYPES = {
    "text/markdown",
    "application/octet-stream",
    "application/x-markdown",
}


def _is_allowed_markdown_file(filename: str, content_type: str | None) -> bool:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_MARKDOWN_SUFFIXES:
        return False
    if content_type is None:
        return True
    return content_type.lower() in ALLOWED_CONTENT_TYPES


def _derive_title_from_filename(filename: str) -> str:
    stem = Path(filename).stem.strip()
    if not stem:
        return "Untitled"
    normalized = stem.replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in normalized.split()) or "Untitled"

def get_project_document_or_404(project_id: int, document_id: int, db: Session) -> Document:
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.project_id == project_id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


def get_document_version_or_404(
    project_id: int,
    document_id: int,
    version_id: int,
    db: Session,
) -> tuple[Document, DocumentVersion]:
    document = get_project_document_or_404(project_id=project_id, document_id=document_id, db=db)
    version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.id == version_id, DocumentVersion.document_id == document.id)
        .first()
    )
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document version not found")
    return document, version


def create_document_version_snapshot(
    db: Session,
    document: Document,
    change_source: str,
    change_summary: str | None = None,
) -> DocumentVersion:
    snapshot = DocumentVersion(
        document_id=document.id,
        version_number=document.current_version,
        markdown_content=document.markdown_content,
        title=document.title,
        filename=document.filename,
        change_source=change_source,
        change_summary=change_summary,
    )
    db.add(snapshot)
    return snapshot


def _raise_integrity_conflict(err: IntegrityError) -> None:
    message = str(err.orig)
    if (
        "uq_documents_project_filename" in message
        or "documents.project_id, documents.filename" in message
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document filename must be unique within a project",
        ) from None
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document update conflict") from None


@router.get("/projects/{project_id}/documents", response_model=list[DocumentRead])
def list_documents(project_id: int, db: Session = Depends(get_db)) -> list[Document]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return (
        db.query(Document)
        .filter(Document.project_id == project_id)
        .order_by(Document.updated_at.desc())
        .all()
    )


@router.post("/projects/{project_id}/documents", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def create_document(project_id: int, payload: DocumentCreate, db: Session = Depends(get_db)) -> Document:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    document = Document(
        project_id=project_id,
        title=payload.title.strip(),
        filename=payload.filename.strip(),
        markdown_content=payload.markdown_content,
        current_version=1,
    )
    db.add(document)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document filename must be unique within a project",
        ) from None

    db.refresh(document)
    return document


@router.post(
    "/projects/{project_id}/import/markdown",
    response_model=MarkdownImportResponse,
)
async def import_markdown_documents(
    project_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> MarkdownImportResponse:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not files:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No files uploaded")

    imported_documents: list[Document] = []
    failures: list[MarkdownImportFailure] = []
    seen_filenames: set[str] = set()

    existing_filenames: set[str] = set()
    for (stored_filename,) in db.query(Document.filename).filter(Document.project_id == project_id).all():
        try:
            existing_filenames.add(sanitize_document_filename(stored_filename))
        except ValueError:
            existing_filenames.add(stored_filename.strip())

    for upload in files:
        raw_name = upload.filename or ""
        try:
            filename = sanitize_document_filename(raw_name)
        except ValueError as err:
            failures.append(
                MarkdownImportFailure(
                    filename=raw_name or "(missing filename)",
                    detail=str(err),
                    code="invalid_filename",
                )
            )
            continue

        if not _is_allowed_markdown_file(filename=filename, content_type=upload.content_type):
            failures.append(
                MarkdownImportFailure(
                    filename=filename,
                    detail="Only .md and .markdown files are supported",
                    code="invalid_file_type",
                )
            )
            continue

        if filename in seen_filenames or filename in existing_filenames:
            failures.append(
                MarkdownImportFailure(
                    filename=filename,
                    detail="Document filename already exists in this project",
                    code="duplicate_filename",
                )
            )
            continue

        content_bytes = await upload.read(MAX_IMPORT_FILE_SIZE_BYTES + 1)
        if len(content_bytes) > MAX_IMPORT_FILE_SIZE_BYTES:
            failures.append(
                MarkdownImportFailure(
                    filename=filename,
                    detail="File exceeds 1 MB limit",
                    code="file_too_large",
                )
            )
            continue

        try:
            markdown_content = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            failures.append(
                MarkdownImportFailure(
                    filename=filename,
                    detail="File must be UTF-8 encoded text",
                    code="invalid_encoding",
                )
            )
            continue

        document = Document(
            project_id=project_id,
            title=_derive_title_from_filename(filename),
            filename=filename,
            markdown_content=markdown_content,
            current_version=1,
        )
        db.add(document)

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            failures.append(
                MarkdownImportFailure(
                    filename=filename,
                    detail="Document filename already exists in this project",
                    code="duplicate_filename",
                )
            )
            continue

        db.refresh(document)
        imported_documents.append(document)
        seen_filenames.add(filename)
        existing_filenames.add(filename)

    if len(files) == 1 and not imported_documents and failures and failures[0].code == "duplicate_filename":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=failures[0].detail)

    return MarkdownImportResponse(
        imported=[DocumentRead.model_validate(document) for document in imported_documents],
        failures=failures,
        imported_count=len(imported_documents),
        failed_count=len(failures),
    )


@router.get("/projects/{project_id}/documents/{document_id}", response_model=DocumentRead)
def get_document(project_id: int, document_id: int, db: Session = Depends(get_db)) -> Document:
    return get_project_document_or_404(project_id=project_id, document_id=document_id, db=db)


@router.get("/projects/{project_id}/documents/{document_id}/export/markdown")
def export_document_markdown(project_id: int, document_id: int, db: Session = Depends(get_db)) -> Response:
    document = get_project_document_or_404(project_id=project_id, document_id=document_id, db=db)
    try:
        safe_filename = sanitize_document_filename(document.filename)
    except ValueError:
        safe_filename = f"document-{document.id}"
    filename = safe_filename if safe_filename.lower().endswith((".md", ".markdown")) else f"{safe_filename}.md"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=document.markdown_content, media_type="text/markdown; charset=utf-8", headers=headers)


@router.patch("/projects/{project_id}/documents/{document_id}", response_model=DocumentRead)
def update_document(
    project_id: int,
    document_id: int,
    payload: DocumentUpdate,
    db: Session = Depends(get_db),
) -> Document:
    document = get_project_document_or_404(project_id=project_id, document_id=document_id, db=db)

    update_data = payload.model_dump(exclude_unset=True)
    next_title = document.title
    next_filename = document.filename
    next_markdown_content = document.markdown_content

    if "title" in update_data and update_data["title"] is not None:
        next_title = update_data["title"].strip()
    if "filename" in update_data and update_data["filename"] is not None:
        next_filename = update_data["filename"].strip()
    if "markdown_content" in update_data and update_data["markdown_content"] is not None:
        next_markdown_content = update_data["markdown_content"]

    if not next_title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Title cannot be empty")
    if not next_filename:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Filename cannot be empty")

    has_changes = (
        document.title != next_title
        or document.filename != next_filename
        or document.markdown_content != next_markdown_content
    )

    if not has_changes:
        return document

    try:
        create_document_version_snapshot(db=db, document=document, change_source=CHANGE_SOURCE_MANUAL)
        document.current_version += 1
        document.title = next_title
        document.filename = next_filename
        document.markdown_content = next_markdown_content
        db.commit()
    except IntegrityError as err:
        db.rollback()
        _raise_integrity_conflict(err)

    db.refresh(document)
    return document


@router.delete("/projects/{project_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(project_id: int, document_id: int, db: Session = Depends(get_db)) -> Response:
    document = get_project_document_or_404(project_id=project_id, document_id=document_id, db=db)

    db.delete(document)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/projects/{project_id}/documents/{document_id}/versions",
    response_model=list[DocumentVersionListItem],
)
def list_document_versions(
    project_id: int,
    document_id: int,
    db: Session = Depends(get_db),
) -> list[DocumentVersion]:
    document = get_project_document_or_404(project_id=project_id, document_id=document_id, db=db)
    return (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number.desc())
        .all()
    )


@router.get(
    "/projects/{project_id}/documents/{document_id}/versions/{version_id}",
    response_model=DocumentVersionRead,
)
def get_document_version(
    project_id: int,
    document_id: int,
    version_id: int,
    db: Session = Depends(get_db),
) -> DocumentVersion:
    _, version = get_document_version_or_404(
        project_id=project_id,
        document_id=document_id,
        version_id=version_id,
        db=db,
    )
    return version


@router.post(
    "/projects/{project_id}/documents/{document_id}/versions/{version_id}/restore",
    response_model=DocumentRead,
)
def restore_document_version(
    project_id: int,
    document_id: int,
    version_id: int,
    db: Session = Depends(get_db),
) -> Document:
    document, version = get_document_version_or_404(
        project_id=project_id,
        document_id=document_id,
        version_id=version_id,
        db=db,
    )

    try:
        create_document_version_snapshot(
            db=db,
            document=document,
            change_source=CHANGE_SOURCE_RESTORE,
            change_summary=f"Restored from version {version.version_number}",
        )
        document.current_version += 1
        document.title = version.title
        document.filename = version.filename
        document.markdown_content = version.markdown_content
        db.commit()
    except IntegrityError as err:
        db.rollback()
        _raise_integrity_conflict(err)

    db.refresh(document)
    return document
