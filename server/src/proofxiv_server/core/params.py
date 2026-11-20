"""Platform parameters the owners can change with a signed statement."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Param

DEFAULTS: dict[str, Any] = {
    # weekly run cadence, used to show "awaiting platform check until <date>"
    "weekly_period_days": 7,
    # priority weights of the weekly queue
    "priority_report": 1000.0,
    "priority_like": 2.0,
    "priority_no_independent_review": 5.0,
    "priority_age_per_day": 1.0,
    "priority_significance": 3.0,  # per point of paper significance (design 6.5)
    # base priority of decisive task kinds (moderation goes first)
    "priority_kind": {
        "moderation": 1000.0,
        "confirm_event": 60.0,
        "novelty_consistency": 50.0,
        "statement_audit": 45.0,
        "novelty_audit": 45.0,
        "anchor_review": 40.0,
        "heldout_review": 40.0,
        "confirm_problem": 30.0,
        "sync": 20.0,
        "digest": 10.0,
    },
    "allowed_vendors": ["anthropic", "openai", "google"],
    "default_lean_image": "proofxiv/lean:4.35.0-mathlib",
    "default_lean_version": "4.35.0",
    "default_checker_image": "proofxiv/checkers:1",
    # every paper needs a positive held-out review (prompt committed by hash) to reach C3
    "heldout_review": True,
    # check bibliographies against arXiv and Crossref (a runner job, no model involved)
    "check_references": True,
}


async def get_param(session: AsyncSession, key: str) -> Any:
    row = await session.get(Param, key)
    if row is not None:
        return row.value.get("v")
    return DEFAULTS.get(key)


async def set_param(session: AsyncSession, key: str, value: Any, statement_id: uuid.UUID | None) -> None:
    row = await session.get(Param, key)
    if row is None:
        session.add(Param(key=key, value={"v": value}, statement_id=statement_id))
    else:
        row.value = {"v": value}
        row.statement_id = statement_id
