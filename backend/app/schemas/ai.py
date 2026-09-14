from pydantic import BaseModel, Field


class GenerateDocumentRequest(BaseModel):
    instruction: str = Field(min_length=1)
    filename: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    selected_document_id: int | None = None


class AcceptGeneratedDocumentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    filename: str = Field(min_length=1, max_length=255)
    markdown_content: str


class ProposeDocumentEditRequest(BaseModel):
    instruction: str = Field(min_length=1)


class AcceptDocumentEditRequest(BaseModel):
    base_version: int = Field(ge=1)
    markdown_content: str
    instruction: str | None = Field(default=None, min_length=1)
