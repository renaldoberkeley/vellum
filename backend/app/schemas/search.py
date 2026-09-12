from pydantic import BaseModel


class SearchResultRead(BaseModel):
    document_id: int
    title: str
    filename: str
    snippet: str
    relevance: float
