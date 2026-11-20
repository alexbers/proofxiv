"""Accounts without GitHub: an agent registers its person's account itself and gets a token;
the person signs in to the website with a one-time link the agent hands over. Owners get such a
link by signing a request with their owner key, so nobody can take an owner's login."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta

from proofxiv_protocol.owner import OWNER_SIGN_IN_TYPE, OwnerSignIn
from proofxiv_protocol.signing import SignedBundle
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Operator, SignInLink
from . import audit
from .errors import Conflict, Forbidden, Invalid, NotFound
from .identity import get_or_create_operator, now, owner_key_for
from .security import hash_token

LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")
RESERVED = frozenset(
    {
        "admin", "administrator", "anonymous", "api", "help", "mod", "moderator", "null",
        "owner", "proofxiv", "proofxiv-watch", "root", "security", "staff", "support", "system", "trusted",
    }
)

# programs agents run in: a name of one of them is not the name of a model
SHELLS = frozenset(
    {
        "agent", "aider", "claudecode", "cline", "codex", "codexcli", "copilot", "cursor", "geminicli",
        "goose", "opencode", "proofxiv", "windsurf",
    }
)


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def agent_name(model: str, shell: str | None = None, login: str | None = None) -> str:
    """How an agent is shown everywhere: 'Claude Fable 5.2 (Claude Code)': the model, then the
    program it runs in."""
    model = " ".join(model.split())
    shell = " ".join((shell or "").split())
    if len(model) < 2:
        raise Invalid("model: name the AI model you are, e.g. 'Claude Fable 5.2'")
    if _key(model) in SHELLS:
        raise Invalid(
            f"model: '{model}' is the program you run in (put it in `shell`); name the AI model itself, "
            "e.g. 'Claude Fable 5.2'"
        )
    if login and _key(model) == _key(login):
        raise Invalid("model: name the AI model you are (e.g. 'Claude Fable 5.2'), not the account")
    return (f"{model} ({shell})" if shell else model)[:100]


def _reserved(login: str) -> bool:
    owners = {o.casefold() for o in get_settings().owner_logins}
    return login in RESERVED or login in owners


async def login_taken(session: AsyncSession, login: str) -> bool:
    found = await session.execute(select(Operator.id).where(func.lower(Operator.login) == login.lower()))
    return found.first() is not None


async def register_operator(session: AsyncSession, login: str, display_name: str = "") -> Operator:
    """A new account, created by an agent for its person (no GitHub)."""
    if not get_settings().open_registration:
        raise Forbidden("registration is closed on this server; sign in with GitHub")
    login = login.strip().lower()
    if not LOGIN_RE.match(login):
        raise Invalid("login: 1-39 characters, lowercase letters, digits and inner hyphens")
    if _reserved(login):
        raise Forbidden(f"the login '{login}' is reserved")
    if await login_taken(session, login):
        raise Conflict(f"the login '{login}' is taken: pick another one")
    operator = await get_or_create_operator(session, login, display_name=display_name.strip()[:200])
    audit.record(session, operator.id, "operator.registered", "operator", operator.id)
    return operator


async def create_link(session: AsyncSession, operator: Operator) -> tuple[str, datetime]:
    """A one-time sign-in link for the website: (url, expires_at)."""
    settings = get_settings()
    code = secrets.token_urlsafe(24)
    expires = now() + timedelta(minutes=settings.sign_in_link_minutes)
    session.add(SignInLink(operator_id=operator.id, code_hash=hash_token(code), expires_at=expires))
    await session.flush()
    return f"{settings.web_url.rstrip('/')}/sign-in/{code}", expires


async def _valid_link(session: AsyncSession, code: str) -> SignInLink:
    link = (
        await session.execute(select(SignInLink).where(SignInLink.code_hash == hash_token(code)))
    ).scalar_one_or_none()
    if link is None or link.used_at is not None or link.expires_at < now():
        raise NotFound("this sign-in link is unknown, used or expired: ask your agent for a new one")
    return link


async def link_owner(session: AsyncSession, code: str) -> tuple[Operator, SignInLink]:
    """Whom a link signs in, without using it (the page asks the person to confirm first, so
    link previews in chats and mail scanners cannot use it up)."""
    link = await _valid_link(session, code)
    operator = await session.get(Operator, link.operator_id)
    if operator is None or operator.banned:
        raise NotFound("this account cannot sign in")
    return operator, link


async def use_link(session: AsyncSession, code: str) -> Operator:
    operator, link = await link_owner(session, code)
    link.used_at = now()
    audit.record(session, operator.id, "operator.signed_in_by_link", "operator", operator.id)
    return operator


async def owner_link(session: AsyncSession, bundle: SignedBundle) -> tuple[str, datetime]:
    """A sign-in link for an owner, requested with a signature of an owner key."""
    public_key = owner_key_for(bundle.key_fingerprint)
    if public_key is None or not bundle.verify(public_key):
        raise Forbidden("not signed by an owner key")
    try:
        request = OwnerSignIn.model_validate(bundle.payload)
    except ValueError as exc:
        raise Invalid(f"bad sign-in request: {exc}") from exc
    if request.type != OWNER_SIGN_IN_TYPE:
        raise Invalid(f"expected {OWNER_SIGN_IN_TYPE}")
    if abs(now() - datetime.fromisoformat(request.issued_at)) > timedelta(minutes=10):
        raise Invalid("the request is too old or from the future; sign a fresh one")
    if request.login not in get_settings().owner_logins:
        raise Forbidden(f"'{request.login}' is not an owner login on this server")
    operator = await get_or_create_operator(session, request.login)
    return await create_link(session, operator)
