from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from app.models.document import Document

_ORG_PREFIX_RE = re.compile(r"^\s*(\d{1,2})(?:\s*[._-]\s*|\s+-\s+)(.+)$")
_MARKDOWN_EXTENSION_RE = re.compile(r"\.(?:md|markdown)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class DocumentReferenceAlias:
    normalized: str
    raw: str


@dataclass(frozen=True)
class DocumentReference:
    document_id: int
    filename: str
    aliases: tuple[DocumentReferenceAlias, ...]


def resolve_explicit_document_ids(documents: list[Document], user_question: str) -> list[int]:
    normalized_question = normalize_reference_text(user_question)
    if not normalized_question:
        return []

    normalized_question_padded = f" {normalized_question} "
    explicit_ids: list[int] = []

    for reference in build_document_references(documents):
        for alias in reference.aliases:
            if not alias.normalized:
                continue
            if f" {alias.normalized} " in normalized_question_padded:
                explicit_ids.append(reference.document_id)
                break

    return explicit_ids


def build_document_references(documents: list[Document]) -> list[DocumentReference]:
    references: list[DocumentReference] = []
    for document in documents:
        aliases = _build_aliases_for_document(document)
        references.append(
            DocumentReference(
                document_id=document.id,
                filename=document.filename,
                aliases=aliases,
            )
        )
    return references


def normalize_reference_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    if not normalized:
        return ""

    normalized = _MARKDOWN_EXTENSION_RE.sub("", normalized)
    normalized = re.sub(r"[\W_]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def strip_organizational_prefix(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""

    match = _ORG_PREFIX_RE.match(candidate)
    if not match:
        return candidate

    remainder = match.group(2).strip()
    if not remainder:
        return candidate

    if not any(char.isalpha() for char in remainder):
        return candidate

    return remainder


def _build_aliases_for_document(document: Document) -> tuple[DocumentReferenceAlias, ...]:
    alias_values: list[str] = []

    filename = document.filename.strip()
    title = document.title.strip()
    stripped_filename = strip_organizational_prefix(filename)

    for value in (filename, stripped_filename, _remove_markdown_extension(filename), _remove_markdown_extension(stripped_filename), title):
        if value and value not in alias_values:
            alias_values.append(value)

    aliases: list[DocumentReferenceAlias] = []
    seen_normalized: set[str] = set()
    for raw_value in alias_values:
        normalized = normalize_reference_text(raw_value)
        if not normalized or normalized in seen_normalized:
            continue
        seen_normalized.add(normalized)
        aliases.append(DocumentReferenceAlias(normalized=normalized, raw=raw_value))

    return tuple(aliases)


def _remove_markdown_extension(value: str) -> str:
    return _MARKDOWN_EXTENSION_RE.sub("", value).strip()
