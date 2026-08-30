from __future__ import annotations

import uuid
from typing import Any


_MIGRATION_NAMESPACE = uuid.UUID("1f270738-0c70-4735-b8b7-fb38cb509424")


def _stringify(value: Any) -> str:
    """Generated function header.

    Function: _stringify
    Path: backend/migrations/postgres/framework/ids.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        return ""
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value).strip()


def deterministic_uuid(building_id: str, *parts: Any) -> uuid.UUID:
    """Generated function header.

    Function: deterministic_uuid
    Path: backend/migrations/postgres/framework/ids.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    token = "|".join([_stringify(building_id), *(_stringify(part) for part in parts)])
    return uuid.uuid5(_MIGRATION_NAMESPACE, token)


def mongo_source_token(document: dict[str, Any], *fallback_fields: str) -> str:
    """Generated function header.

    Function: mongo_source_token
    Path: backend/migrations/postgres/framework/ids.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if "_id" in document and document["_id"] is not None:
        return _stringify(document["_id"])
    for field in fallback_fields:
        value = document.get(field)
        if value not in (None, ""):
            return _stringify(value)
    stable_parts = [f"{field}={_stringify(document.get(field))}" for field in sorted(fallback_fields)]
    return "|".join(stable_parts)
