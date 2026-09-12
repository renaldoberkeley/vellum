from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_create_list_update_delete_project(client: TestClient) -> None:
    create_response = client.post(
        "/api/projects",
        json={"name": "Vellum", "description": "Initial project"},
    )
    assert create_response.status_code == 201
    project = create_response.json()
    assert project["name"] == "Vellum"

    list_response = client.get("/api/projects")
    assert list_response.status_code == 200
    projects = list_response.json()
    assert len(projects) == 1

    project_id = project["id"]
    update_response = client.patch(
        f"/api/projects/{project_id}",
        json={"name": "Vellum Updated"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Vellum Updated"

    delete_response = client.delete(f"/api/projects/{project_id}")
    assert delete_response.status_code == 204


def test_create_and_update_document(client: TestClient) -> None:
    project_response = client.post("/api/projects", json={"name": "Docs"})
    project_id = project_response.json()["id"]
    other_project_response = client.post("/api/projects", json={"name": "Other"})
    other_project_id = other_project_response.json()["id"]

    create_doc_response = client.post(
        f"/api/projects/{project_id}/documents",
        json={
            "title": "Vision",
            "filename": "vision.md",
            "markdown_content": "# Vision",
        },
    )
    assert create_doc_response.status_code == 201
    document = create_doc_response.json()
    assert document["filename"] == "vision.md"

    list_doc_response = client.get(f"/api/projects/{project_id}/documents")
    assert list_doc_response.status_code == 200
    assert len(list_doc_response.json()) == 1

    document_id = document["id"]
    get_doc_response = client.get(f"/api/projects/{project_id}/documents/{document_id}")
    assert get_doc_response.status_code == 200

    update_doc_response = client.patch(
        f"/api/projects/{project_id}/documents/{document_id}",
        json={"markdown_content": "# Updated Vision"},
    )
    assert update_doc_response.status_code == 200
    assert update_doc_response.json()["markdown_content"] == "# Updated Vision"

    mismatched_response = client.get(f"/api/projects/{other_project_id}/documents/{document_id}")
    assert mismatched_response.status_code == 404

    duplicate_doc_response = client.post(
        f"/api/projects/{project_id}/documents",
        json={
            "title": "Vision 2",
            "filename": "vision.md",
            "markdown_content": "# Duplicate",
        },
    )
    assert duplicate_doc_response.status_code == 409

    delete_doc_response = client.delete(f"/api/projects/{project_id}/documents/{document_id}")
    assert delete_doc_response.status_code == 204
