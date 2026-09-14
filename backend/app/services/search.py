from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from sqlalchemy import Float, case, cast, func, literal, or_
from sqlalchemy.orm import Session

from app.models.document import Document
from app.schemas.search import SearchResultRead


@dataclass
class SearchResult:
    document_id: int
    title: str
    filename: str
    snippet: str
    relevance: float


class SearchService(Protocol):
    def search(self, project_id: int, query: str, limit: int = 25) -> list[SearchResult]:
        ...


class PostgresLexicalSearchService:
    def __init__(self, db: Session):
        self.db = db

    def search(self, project_id: int, query: str, limit: int = 25) -> list[SearchResult]:
        cleaned_query = query.strip()
        if not cleaned_query:
            return []

        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            return self._search_postgres(project_id=project_id, query=cleaned_query, limit=limit)
        return self._search_fallback(project_id=project_id, query=cleaned_query, limit=limit)

    def _search_postgres(self, project_id: int, query: str, limit: int) -> list[SearchResult]:
        terms = _extract_search_terms(query)
        if not terms:
            return []

        ts_query_any = func.to_tsquery("simple", " | ".join(f"{term}:*" for term in terms))
        ts_query_phrase = func.plainto_tsquery("simple", query)
        searchable_text = (
            func.coalesce(Document.title, "")
            + literal(" ")
            + func.coalesce(Document.filename, "")
            + literal(" ")
            + func.coalesce(Document.markdown_content, "")
        )
        tsvector = func.to_tsvector("simple", searchable_text)
        rank = func.ts_rank_cd(tsvector, ts_query_any) + (func.ts_rank_cd(tsvector, ts_query_phrase) * 1.5)
        snippet = func.ts_headline(
            "simple",
            func.coalesce(Document.markdown_content, ""),
            ts_query_any,
            "MaxWords=24, MinWords=8",
        )

        rows = (
            self.db.query(
                Document.id.label("document_id"),
                Document.title.label("title"),
                Document.filename.label("filename"),
                snippet.label("snippet"),
                rank.label("relevance"),
            )
            .filter(Document.project_id == project_id)
            .filter(tsvector.op("@@")(ts_query_any))
            .order_by(rank.desc(), Document.updated_at.desc())
            .limit(limit)
            .all()
        )

        return [
            SearchResult(
                document_id=row.document_id,
                title=row.title,
                filename=row.filename,
                snippet=row.snippet or row.title,
                relevance=float(row.relevance or 0.0),
            )
            for row in rows
        ]

    def _search_fallback(self, project_id: int, query: str, limit: int) -> list[SearchResult]:
        terms = _extract_search_terms(query)
        if not terms:
            return []

        title_scores = []
        filename_scores = []
        content_scores = []
        conditions = []

        for term in terms:
            like_pattern = f"%{term}%"
            title_match = Document.title.ilike(like_pattern)
            filename_match = Document.filename.ilike(like_pattern)
            content_match = Document.markdown_content.ilike(like_pattern)

            title_scores.append(case((title_match, 3), else_=0))
            filename_scores.append(case((filename_match, 2), else_=0))
            content_scores.append(case((content_match, 1), else_=0))
            conditions.extend([title_match, filename_match, content_match])

        title_relevance = sum(title_scores[1:], title_scores[0])
        filename_relevance = sum(filename_scores[1:], filename_scores[0])
        content_relevance = sum(content_scores[1:], content_scores[0])

        relevance = cast(title_relevance + filename_relevance + content_relevance, Float)

        rows = (
            self.db.query(
                Document.id.label("document_id"),
                Document.title.label("title"),
                Document.filename.label("filename"),
                Document.markdown_content.label("markdown_content"),
                relevance.label("relevance"),
            )
            .filter(Document.project_id == project_id)
            .filter(or_(*conditions))
            .order_by((title_relevance + filename_relevance + content_relevance).desc(), Document.updated_at.desc())
            .limit(limit)
            .all()
        )

        results: list[SearchResult] = []
        for row in rows:
            content = row.markdown_content or ""
            snippet = _build_snippet(content=content, query=query, terms=terms)
            if not snippet:
                snippet = row.title
            results.append(
                SearchResult(
                    document_id=row.document_id,
                    title=row.title,
                    filename=row.filename,
                    snippet=snippet,
                    relevance=float(row.relevance or 0),
                )
            )

        return results


def _build_snippet(content: str, query: str, terms: list[str], window: int = 72) -> str:
    if not content:
        return ""

    lower_content = content.lower()
    indexes = [lower_content.find(term) for term in terms]
    valid_indexes = [index for index in indexes if index >= 0]
    lower_query = query.lower()
    phrase_index = lower_content.find(lower_query)
    if phrase_index >= 0:
        valid_indexes.append(phrase_index)
    index = min(valid_indexes) if valid_indexes else -1
    if index < 0:
        return " ".join(content.split())[: window * 2]

    start = max(index - window, 0)
    end = min(index + len(query) + window, len(content))
    snippet = content[start:end].strip()
    snippet = " ".join(snippet.split())
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(content):
        snippet = f"{snippet}..."
    return snippet


def _extract_search_terms(query: str) -> list[str]:
    terms = re.findall(r"[a-z0-9_]+", query.lower())
    stop_words = {
        "about",
        "a",
        "against",
        "all",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "compare",
        "could",
        "does",
        "for",
        "from",
        "how",
        "if",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "say",
        "the",
        "their",
        "there",
        "this",
        "to",
        "us",
        "using",
        "vs",
        "we",
        "what",
        "which",
        "why",
        "with",
        "would",
    }
    filtered = [term for term in terms if term not in stop_words]
    seen: set[str] = set()
    deduped: list[str] = []
    for term in filtered:
        if term in seen:
            continue
        seen.add(term)
        deduped.append(term)
    return deduped


def get_search_service(db: Session) -> SearchService:
    return PostgresLexicalSearchService(db)


def to_search_response(results: list[SearchResult]) -> list[SearchResultRead]:
    return [
        SearchResultRead(
            document_id=result.document_id,
            title=result.title,
            filename=result.filename,
            snippet=result.snippet,
            relevance=result.relevance,
        )
        for result in results
    ]
