from pydantic import BaseModel, ConfigDict

from app.schemas.document import DocumentRead


class MarkdownImportFailure(BaseModel):
    filename: str
    detail: str
    code: str


class MarkdownImportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    imported: list[DocumentRead]
    failures: list[MarkdownImportFailure]
    imported_count: int
    failed_count: int
