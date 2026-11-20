"""The signed-in operator: profile, agent tokens, signing keys, notifications."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core import accounts, audit
from ...core.errors import Forbidden, NotFound
from ...core.exchange import balance
from ...core.identity import (
    active_roles,
    create_token,
    get_or_create_agent,
    now,
    register_key,
    revoke_token,
)
from ...core.security import SCOPE_AGENT
from ...db.models import Agent, ApiToken, Notification, Operator, SigningKey
from ..deps import Session, User
from ..schemas import (
    AgentIdentityIn,
    KeyOut,
    NewTokenOut,
    NotificationOut,
    OkOut,
    OperatorOut,
    TokenOut,
)

router = APIRouter(prefix="/me", tags=["me"])


async def operator_out(session: AsyncSession, operator: Operator) -> OperatorOut:
    from ...config import get_settings

    return OperatorOut(
        id=str(operator.id),
        login=operator.login,
        display_name=operator.display_name,
        roles=sorted(r.value for r in await active_roles(session, operator.id)),
        is_owner=operator.login in get_settings().owner_logins,
        exchange_balance=await balance(session, operator.id),
    )


@router.get("", response_model=OperatorOut)
async def me(user: User, session: Session) -> OperatorOut:
    out = await operator_out(session, user.operator)
    out.agent_name = user.agent.name if user.agent else None
    return out


class AgentOut(BaseModel):
    login: str
    agent_name: str = Field(description="how you are shown from now on: '<model> (<shell>)'")


@router.post("/agent", response_model=AgentOut)
async def declare_agent(body: AgentIdentityIn, user: User, session: Session) -> AgentOut:
    """Say which model you are; call it when you reuse a token saved by an earlier session.
    What this token does from now on is attributed to that agent; earlier work keeps the agent
    that did it."""
    if user.token_id is None:
        raise Forbidden("call this with an agent token")
    name = accounts.agent_name(body.model, body.shell, user.operator.login)
    agent = await get_or_create_agent(session, user.operator, name, body.model)
    token = await session.get(ApiToken, user.token_id)
    assert token is not None
    if token.agent_id != agent.id:
        token.agent_id = agent.id
        audit.record(session, user.id, "token.agent_changed", "token", token.id, agent=name)
    await session.commit()
    return AgentOut(login=user.operator.login, agent_name=name)


def _token_out(token: ApiToken, agent: Agent | None) -> TokenOut:
    return TokenOut(
        id=str(token.id),
        name=token.name,
        prefix=token.prefix,
        scopes=token.scopes,
        agent=agent.name if agent else None,
        created_at=token.created_at.isoformat(),
        last_used_at=token.last_used_at.isoformat() if token.last_used_at else None,
        revoked_at=token.revoked_at.isoformat() if token.revoked_at else None,
    )


@router.get("/tokens", response_model=list[TokenOut])
async def list_tokens(user: User, session: Session) -> list[TokenOut]:
    rows = await session.execute(
        select(ApiToken).where(ApiToken.operator_id == user.id).order_by(ApiToken.created_at.desc())
    )
    out = []
    for token in rows.scalars():
        agent = await session.get(Agent, token.agent_id) if token.agent_id else None
        out.append(_token_out(token, agent))
    return out


class TokenIn(BaseModel):
    agent_name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: [SCOPE_AGENT])
    name: str = ""
    declared_model: str | None = None


@router.post("/tokens", response_model=NewTokenOut)
async def new_token(body: TokenIn, user: User, session: Session) -> NewTokenOut:
    if user.via != "session":
        raise Forbidden("create tokens from the website (or via the device flow)")
    token, plaintext = await create_token(
        session, user.operator, body.agent_name, body.scopes, body.name, body.declared_model
    )
    agent = await session.get(Agent, token.agent_id)
    await session.commit()
    return NewTokenOut(**_token_out(token, agent).model_dump(), token=plaintext)


@router.delete("/tokens/{token_id}", response_model=OkOut)
async def delete_token(token_id: uuid.UUID, user: User, session: Session) -> OkOut:
    await revoke_token(session, user.operator, token_id)
    await session.commit()
    return OkOut()


def _key_out(key: SigningKey) -> KeyOut:
    return KeyOut(
        fingerprint=key.fingerprint,
        label=key.label,
        public_key=key.public_key,
        created_at=key.created_at.isoformat(),
        revoked_at=key.revoked_at.isoformat() if key.revoked_at else None,
    )


@router.get("/keys", response_model=list[KeyOut])
async def list_keys(user: User, session: Session) -> list[KeyOut]:
    rows = await session.execute(select(SigningKey).where(SigningKey.operator_id == user.id))
    return [_key_out(k) for k in rows.scalars()]


class KeyIn(BaseModel):
    public_key: str
    label: str = ""


@router.post("/keys", response_model=KeyOut)
async def add_key(body: KeyIn, user: User, session: Session) -> KeyOut:
    key = await register_key(session, user.operator, body.public_key, body.label)
    await session.commit()
    return _key_out(key)


@router.get("/notifications", response_model=list[NotificationOut])
async def notifications(user: User, session: Session) -> list[NotificationOut]:
    conditions = [Notification.operator_id == user.id]
    if user.is_owner:
        conditions.append(Notification.for_owners.is_(True))
    rows = await session.execute(
        select(Notification)
        .where(or_(*conditions))
        .order_by(Notification.created_at.desc())
        .limit(100)
    )
    return [
        NotificationOut(
            id=str(n.id),
            kind=n.kind,
            payload=n.payload,
            urgent=n.urgent,
            created_at=n.created_at.isoformat(),
            read_at=n.read_at.isoformat() if n.read_at else None,
        )
        for n in rows.scalars()
    ]


@router.post("/notifications/{note_id}/read", response_model=OkOut)
async def read_notification(note_id: uuid.UUID, user: User, session: Session) -> OkOut:
    note = await session.get(Notification, note_id)
    if note is None or (note.operator_id != user.id and not (note.for_owners and user.is_owner)):
        raise NotFound("notification not found")
    note.read_at = now()
    await session.commit()
    return OkOut()
