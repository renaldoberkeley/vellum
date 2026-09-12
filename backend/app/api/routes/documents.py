from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.document import Document
from app.models.project import Project
from app.schemas.document import DocumentCreate, DocumentRead, DocumentUpdate

router = APIRouter(tags=["documents"])


def get_project_document_or_404(project_id: int, document_id: int, db: Session) -> Document:
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.project_id == project_id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


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


@router.get("/projects/{project_id}/documents/{document_id}", response_model=DocumentRead)
def get_document(project_id: int, document_id: int, db: Session = Depends(get_db)) -> Document:
    return get_project_document_or_404(project_id=project_id, document_id=document_id, db=db)


@router.patch("/projects/{project_id}/documents/{document_id}", response_model=DocumentRead)
def update_document(
    project_id: int,
    document_id: int,
    payload: DocumentUpdate,
    db: Session = Depends(get_db),
) -> Document:
    document = get_project_document_or_404(project_id=project_id, document_id=document_id, db=db)

    update_data = payload.model_dump(exclude_unset=True)
    if "title" in update_data and update_data["title"] is not None:
        update_data["title"] = update_data["title"].strip()
    if "filename" in update_data and update_data["filename"] is not None:
        update_data["filename"] = update_data["filename"].strip()

    for key, value in update_data.items():
        setattr(document, key, value)

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


@router.delete("/projects/{project_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(project_id: int, document_id: int, db: Session = Depends(get_db)) -> Response:
    document = get_project_document_or_404(project_id=project_id, document_id=document_id, db=db)

    db.delete(document)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
