from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.document import Document
from app.models.document_version import DocumentVersion


@pytest.fixture()
def test_env() -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
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

    with TestClient(app) as test_client:
        yield test_client, testing_session_local

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def _create_project(client: TestClient, name: str = "Docs") -> int:
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def _create_document(
    client: TestClient,
    project_id: int,
    title: str = "Vision",
    filename: str = "vision.md",
    markdown_content: str = "# Vision",
) -> dict:
    response = client.post(
        f"/api/projects/{project_id}/documents",
        json={
            "title": title,
            "filename": filename,
            "markdown_content": markdown_content,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_create_list_update_delete_project(test_env: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _ = test_env
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


def test_document_creation_starts_at_version_one(test_env: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _ = test_env
    project_id = _create_project(client)
    document = _create_document(client, project_id)

    assert document["current_version"] == 1

    versions_response = client.get(f"/api/projects/{project_id}/documents/{document['id']}/versions")
    assert versions_response.status_code == 200
    assert versions_response.json() == []


def test_save_changed_content_creates_version_history(
    test_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = test_env
    project_id = _create_project(client)
    document = _create_document(client, project_id)

    update_response = client.patch(
        f"/api/projects/{project_id}/documents/{document['id']}",
        json={
            "title": "Vision Updated",
            "filename": "vision-updated.md",
            "markdown_content": "# Updated Vision",
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["current_version"] == 2

    versions_response = client.get(f"/api/projects/{project_id}/documents/{document['id']}/versions")
    assert versions_response.status_code == 200
    versions = versions_response.json()
    assert len(versions) == 1
    assert versions[0]["version_number"] == 1
    assert versions[0]["change_source"] == "manual"

    version_id = versions[0]["id"]
    version_detail_response = client.get(
        f"/api/projects/{project_id}/documents/{document['id']}/versions/{version_id}"
    )
    assert version_detail_response.status_code == 200
    version_detail = version_detail_response.json()
    assert version_detail["title"] == "Vision"
    assert version_detail["filename"] == "vision.md"
    assert version_detail["markdown_content"] == "# Vision"


def test_identical_save_does_not_create_new_version(
    test_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = test_env
    project_id = _create_project(client)
    document = _create_document(client, project_id)

    update_response = client.patch(
        f"/api/projects/{project_id}/documents/{document['id']}",
        json={
            "title": "Vision",
            "filename": "vision.md",
            "markdown_content": "# Vision",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["current_version"] == 1

    versions_response = client.get(f"/api/projects/{project_id}/documents/{document['id']}/versions")
    assert versions_response.status_code == 200
    assert versions_response.json() == []


def test_versions_sequential_and_restore_preserves_history(
    test_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = test_env
    project_id = _create_project(client)
    document = _create_document(client, project_id)

    first_update = client.patch(
        f"/api/projects/{project_id}/documents/{document['id']}",
        json={"title": "Vision V2", "filename": "vision-v2.md", "markdown_content": "# Vision V2"},
    )
    assert first_update.status_code == 200
    assert first_update.json()["current_version"] == 2

    second_update = client.patch(
        f"/api/projects/{project_id}/documents/{document['id']}",
        json={"title": "Vision V3", "filename": "vision-v3.md", "markdown_content": "# Vision V3"},
    )
    assert second_update.status_code == 200
    assert second_update.json()["current_version"] == 3

    versions_before_restore_response = client.get(
        f"/api/projects/{project_id}/documents/{document['id']}/versions"
    )
    assert versions_before_restore_response.status_code == 200
    versions_before_restore = versions_before_restore_response.json()
    assert [version["version_number"] for version in versions_before_restore] == [2, 1]

    target_version = next(version for version in versions_before_restore if version["version_number"] == 1)
    restore_response = client.post(
        f"/api/projects/{project_id}/documents/{document['id']}/versions/{target_version['id']}/restore"
    )
    assert restore_response.status_code == 200
    restored_document = restore_response.json()
    assert restored_document["current_version"] == 4
    assert restored_document["title"] == "Vision"
    assert restored_document["filename"] == "vision.md"
    assert restored_document["markdown_content"] == "# Vision"

    versions_after_restore_response = client.get(
        f"/api/projects/{project_id}/documents/{document['id']}/versions"
    )
    assert versions_after_restore_response.status_code == 200
    versions_after_restore = versions_after_restore_response.json()
    assert [version["version_number"] for version in versions_after_restore] == [3, 2, 1]

    restore_snapshot = next(version for version in versions_after_restore if version["version_number"] == 3)
    assert restore_snapshot["change_source"] == "restore"
    assert target_version["id"] in [version["id"] for version in versions_after_restore]


def test_document_and_version_scoping_returns_404(
    test_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = test_env
    project_id = _create_project(client, "Project A")
    other_project_id = _create_project(client, "Project B")
    document = _create_document(client, project_id)

    update_response = client.patch(
        f"/api/projects/{project_id}/documents/{document['id']}",
        json={"markdown_content": "# Changed"},
    )
    assert update_response.status_code == 200

    versions_response = client.get(f"/api/projects/{project_id}/documents/{document['id']}/versions")
    version_id = versions_response.json()[0]["id"]

    mismatched_doc = client.get(f"/api/projects/{other_project_id}/documents/{document['id']}")
    assert mismatched_doc.status_code == 404

    mismatched_versions = client.get(f"/api/projects/{other_project_id}/documents/{document['id']}/versions")
    assert mismatched_versions.status_code == 404

    mismatched_get_version = client.get(
        f"/api/projects/{other_project_id}/documents/{document['id']}/versions/{version_id}"
    )
    assert mismatched_get_version.status_code == 404

    mismatched_restore = client.post(
        f"/api/projects/{other_project_id}/documents/{document['id']}/versions/{version_id}/restore"
    )
    assert mismatched_restore.status_code == 404


def test_delete_document_cascades_versions(test_env: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, session_factory = test_env
    project_id = _create_project(client)
    document = _create_document(client, project_id)

    update_response = client.patch(
        f"/api/projects/{project_id}/documents/{document['id']}",
        json={"markdown_content": "# Updated"},
    )
    assert update_response.status_code == 200

    delete_response = client.delete(f"/api/projects/{project_id}/documents/{document['id']}")
    assert delete_response.status_code == 204

    with session_factory() as db:
        versions_count = db.scalar(
            select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == document["id"])
        )
        assert versions_count == 0


def test_delete_project_cascades_documents_and_versions(
    test_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = test_env
    project_id = _create_project(client)
    document = _create_document(client, project_id)

    update_response = client.patch(
        f"/api/projects/{project_id}/documents/{document['id']}",
        json={"markdown_content": "# Updated"},
    )
    assert update_response.status_code == 200

    delete_project_response = client.delete(f"/api/projects/{project_id}")
    assert delete_project_response.status_code == 204

    with session_factory() as db:
        documents_count = db.scalar(
            select(func.count()).select_from(Document).where(Document.project_id == project_id)
        )
        versions_count = db.scalar(
            select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == document["id"])
        )
        assert documents_count == 0
        assert versions_count == 0
