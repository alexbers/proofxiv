"""Content-addressed artifact storage on local disk (sha256 -> file)."""

from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Artifact

TEXT_SUFFIXES = {".lean", ".md", ".tex", ".bib", ".txt", ".json", ".yaml", ".yml", ".py", ".csv"}


def artifacts_root() -> Path:
    root = get_settings().data_dir / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def path_for(sha256: str) -> Path:
    return artifacts_root() / sha256[:2] / sha256[2:4] / sha256


def guess_media_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return "text/plain; charset=utf-8"
    media, _ = mimetypes.guess_type(name)
    return media or "application/octet-stream"


async def store_bytes(
    session: AsyncSession, data: bytes, name: str, uploaded_by: object | None, embargoed: bool = False
) -> str:
    """Store content once by its hash. `embargoed` applies to a new artifact only: an upload
    cannot hide a file that is already there."""
    sha = hashlib.sha256(data).hexdigest()
    target = path_for(sha)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    if await session.get(Artifact, sha) is None:
        session.add(
            Artifact(
                sha256=sha,
                size=len(data),
                media_type=guess_media_type(name),
                original_name=Path(name).name[:300],
                uploaded_by=uploaded_by,
                embargoed=embargoed,
            )
        )
    return sha


def read_bytes(sha256: str) -> bytes:
    return path_for(sha256).read_bytes()


def read_text(sha256: str, limit: int = 2_000_000) -> str:
    data = path_for(sha256).read_bytes()[:limit]
    return data.decode("utf-8", errors="replace")
