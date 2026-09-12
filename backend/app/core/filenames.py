from __future__ import annotations

import re

SAFE_FILENAME_CHARS = re.compile(r"[^\w\s.()\-]", flags=re.UNICODE)


def sanitize_document_filename(raw_filename: str) -> str:
    if "\r" in raw_filename or "\n" in raw_filename:
        raise ValueError("Filename cannot contain CR or LF characters")

    normalized = raw_filename.replace("\\", "/")
    basename = normalized.rsplit("/", maxsplit=1)[-1].strip()
    if not basename:
        raise ValueError("Filename is required")

    sanitized = SAFE_FILENAME_CHARS.sub("", basename)
    sanitized = " ".join(sanitized.split())
    sanitized = sanitized.strip(".")

    if not sanitized:
        raise ValueError("Filename is required")

    return sanitized
