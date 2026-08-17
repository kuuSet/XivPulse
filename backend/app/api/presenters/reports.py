"""JSON-ready digest views."""

from __future__ import annotations

from typing import Any

from ...models import Digest


def _digest_wire(digest: Digest | None) -> dict[str, Any] | None:
    if digest is None:
        return None
    return {
        "id": digest.id,
        "profile_id": digest.profile_id,
        "digest_date": digest.digest_date,
        "title": digest.title,
        "summary": digest.summary,
        "paper_ids": digest.paper_ids,
        "stats": digest.stats,
        "status": digest.status,
        "created_at": digest.created_at,
    }
