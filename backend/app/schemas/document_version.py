from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentVersionListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    version_number: int
    change_source: str
    change_summary: str | None
    created_at: datetime


class DocumentVersionRead(DocumentVersionListItem):
    title: str
    filename: str
    markdown_content: str
