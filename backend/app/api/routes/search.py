from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.project import Project
from app.schemas.search import SearchResultRead
from app.services.search import get_search_service, to_search_response

router = APIRouter(tags=["search"])


@router.get("/projects/{project_id}/search", response_model=list[SearchResultRead])
def search_project_documents(
    project_id: int,
    q: str = Query(min_length=1),
    db: Session = Depends(get_db),
) -> list[SearchResultRead]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    query = q.strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Query cannot be empty")

    service = get_search_service(db)
    results = service.search(project_id=project_id, query=query)
    return to_search_response(results)
