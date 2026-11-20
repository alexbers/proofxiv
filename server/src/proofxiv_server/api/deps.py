from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..core.errors import Unauthorized
from ..core.identity import Principal, resolve_token
from ..core.security import SCOPE_AGENT, decode_session
from ..db.models import Operator
from ..db.session import sessionmaker

SESSION_COOKIE = "pxv_session"


async def db() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as session:
        yield session


Session = Annotated[AsyncSession, Depends(db)]


async def principal_from_request(request: Request, session: AsyncSession) -> Principal | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return await resolve_token(session, auth[7:].strip())
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        operator_id = decode_session(cookie, get_settings().session_secret)
        if operator_id is not None:
            operator = await session.get(Operator, operator_id)
            if operator is not None and not operator.banned:
                return Principal(operator=operator, scopes=frozenset({SCOPE_AGENT}), via="session")
    return None


async def optional_principal(request: Request, session: Session) -> Principal | None:
    return await principal_from_request(request, session)


async def require_principal(request: Request, session: Session) -> Principal:
    principal = await principal_from_request(request, session)
    if principal is None:
        raise Unauthorized("sign in or pass an agent token")
    return principal


MaybeUser = Annotated[Principal | None, Depends(optional_principal)]
User = Annotated[Principal, Depends(require_principal)]
