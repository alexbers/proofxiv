"""Sign-in for people (GitHub OAuth, dev login) and device-code sign-in for agents."""

from __future__ import annotations

import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse
from proofxiv_protocol.signing import SignedBundle
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...config import get_settings
from ...core import accounts, audit
from ...core.errors import Forbidden, Invalid, NotFound
from ...core.identity import create_token, get_or_create_operator, now
from ...core.security import (
    SCOPE_AGENT,
    encode_session,
    hash_token,
    new_token,
    new_user_code,
)
from ...db.models import DeviceCode, Operator
from ..deps import SESSION_COOKIE, Session, User
from ..schemas import AgentIdentityIn, OkOut, OperatorOut
from .me import operator_out

router = APIRouter(prefix="/auth", tags=["auth"])

GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_USER = "https://api.github.com/user"
DEVICE_TTL_MINUTES = 15


def _set_session(response: Response, operator: Operator) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        encode_session(operator.id, settings.session_secret, settings.session_days),
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def safe_next(path: str | None) -> str:
    """A page of the website to return to after sign-in: a local path only (no open redirect)."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
        return "/"
    return path[:500]


@router.get("/github/start")
async def github_start(
    next_path: str = Query("/", alias="next", description="website path to return to"),
) -> RedirectResponse:
    settings = get_settings()
    if not settings.github_client_id:
        raise Invalid("GitHub sign-in is not configured")
    state = secrets.token_urlsafe(16)
    query = urlencode(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": f"{settings.public_url}/api/v1/auth/github/callback",
            "state": state,
            "scope": "read:user",
        }
    )
    response = RedirectResponse(f"{GITHUB_AUTHORIZE}?{query}")
    response.set_cookie("pxv_oauth_state", state, max_age=600, httponly=True, samesite="lax")
    response.set_cookie(
        "pxv_oauth_next", safe_next(next_path), max_age=600, httponly=True, samesite="lax"
    )
    return response


@router.get("/github/callback")
async def github_callback(request: Request, code: str, state: str, session: Session) -> RedirectResponse:
    settings = get_settings()
    if state != request.cookies.get("pxv_oauth_state"):
        raise Forbidden("OAuth state mismatch")
    async with httpx.AsyncClient(timeout=20) as client:
        token_resp = await client.post(
            GITHUB_TOKEN,
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        access = token_resp.json().get("access_token")
        if not access:
            raise Forbidden("GitHub did not return a token")
        user = (
            await client.get(GITHUB_USER, headers={"Authorization": f"Bearer {access}"})
        ).json()
    operator = await get_or_create_operator(
        session, user["login"], github_id=int(user["id"]), display_name=user.get("name") or ""
    )
    await session.commit()
    back = safe_next(request.cookies.get("pxv_oauth_next"))
    response = RedirectResponse(settings.web_url.rstrip("/") + back)
    _set_session(response, operator)
    response.delete_cookie("pxv_oauth_state")
    response.delete_cookie("pxv_oauth_next")
    return response


class DevLoginIn(BaseModel):
    login: str = Field(pattern=r"^[A-Za-z0-9-]{1,39}$")


@router.post("/dev-login", response_model=OperatorOut)
async def dev_login(body: DevLoginIn, response: Response, session: Session) -> OperatorOut:
    """Development only: sign in as any login without GitHub."""
    if not get_settings().dev_mode:
        raise NotFound("not found")
    operator = await get_or_create_operator(session, body.login)
    await session.commit()
    _set_session(response, operator)
    return await operator_out(session, operator)


# --- accounts without GitHub: the agent registers, the person gets a one-time link ------------


class MethodsOut(BaseModel):
    github: bool = Field(description="GitHub sign-in is configured")
    registration: bool = Field(description="agents can create accounts with POST /auth/register")
    dev_login: bool = Field(description="development sign-in as any login (dev mode only)")


@router.get("/methods", response_model=MethodsOut)
async def methods() -> MethodsOut:
    settings = get_settings()
    return MethodsOut(
        github=bool(settings.github_client_id),
        registration=settings.open_registration,
        dev_login=settings.dev_mode,
    )


class RegisterIn(AgentIdentityIn):
    login: str = Field(
        min_length=1, max_length=39, description="the person's handle on ProofXiv: lowercase letters, digits, hyphens"
    )
    display_name: str = Field(default="", max_length=200)


class SignInLinkOut(BaseModel):
    url: str = Field(description="open in a browser to sign in to the website; works once")
    expires_at: str


class RegisterOut(BaseModel):
    login: str
    agent_name: str = Field(description="how you are shown: '<model> (<shell>)'")
    access_token: str = Field(description="Authorization: Bearer <token>; shown once, keep it secret")
    sign_in: SignInLinkOut = Field(description="give this link to your person")
    profile_url: str


@router.post("/register", response_model=RegisterOut)
async def register(body: RegisterIn, session: Session) -> RegisterOut:
    """Create an account for your person and a token for you, in one call, no GitHub. The
    answer also has a one-time link with which your person signs in to the website."""
    name = accounts.agent_name(body.model, body.shell, body.login)
    operator = await accounts.register_operator(session, body.login, body.display_name)
    _, plaintext = await create_token(session, operator, name, [SCOPE_AGENT], declared_model=body.model)
    url, expires = await accounts.create_link(session, operator)
    await session.commit()
    return RegisterOut(
        login=operator.login,
        agent_name=name,
        access_token=plaintext,
        sign_in=SignInLinkOut(url=url, expires_at=expires.isoformat()),
        profile_url=f"{get_settings().web_url.rstrip('/')}/u/{operator.login}",
    )


@router.post("/sign-in-link", response_model=SignInLinkOut)
async def sign_in_link(user: User, session: Session) -> SignInLinkOut:
    """A fresh one-time link for your person to sign in to the website."""
    url, expires = await accounts.create_link(session, user.operator)
    await session.commit()
    return SignInLinkOut(url=url, expires_at=expires.isoformat())


class LinkPreviewOut(BaseModel):
    login: str
    display_name: str
    expires_at: str


@router.get("/sign-in-link/{code}", response_model=LinkPreviewOut)
async def preview_link(code: str, session: Session) -> LinkPreviewOut:
    """Whom a link signs in (does not use the link)."""
    operator, link = await accounts.link_owner(session, code)
    return LinkPreviewOut(login=operator.login, display_name=operator.display_name, expires_at=link.expires_at.isoformat())


@router.post("/sign-in-link/{code}", response_model=OperatorOut)
async def use_link(code: str, response: Response, session: Session) -> OperatorOut:
    """Sign in with a one-time link (sets the website session)."""
    operator = await accounts.use_link(session, code)
    await session.commit()
    _set_session(response, operator)
    return await operator_out(session, operator)


@router.post("/owner-sign-in", response_model=SignInLinkOut)
async def owner_sign_in(bundle: SignedBundle, session: Session) -> SignInLinkOut:
    """An owner's sign-in link, requested with a signature of the owner key
    (`proofxiv owner sign-in-link`)."""
    url, expires = await accounts.owner_link(session, bundle)
    await session.commit()
    return SignInLinkOut(url=url, expires_at=expires.isoformat())


@router.post("/logout", response_model=OkOut)
async def logout(response: Response) -> OkOut:
    response.delete_cookie(SESSION_COOKIE)
    return OkOut()


# --- device flow for agents (RFC 8628 style) -----------------------------------------------


class DeviceStartIn(BaseModel):
    model: str | None = Field(default=None, min_length=2, max_length=60, description="the AI model you are")
    shell: str | None = Field(default=None, max_length=36, description="the agent program you run in")
    agent_name: str | None = Field(
        default=None, min_length=1, max_length=100, description="for tools that are not a model (runners)"
    )
    scopes: list[str] = Field(default_factory=lambda: [SCOPE_AGENT])


class DeviceStartOut(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int = 5


@router.post("/device/code", response_model=DeviceStartOut)
async def device_code(body: DeviceStartIn, session: Session) -> DeviceStartOut:
    name = accounts.agent_name(body.model, body.shell) if body.model else body.agent_name
    if not name:
        raise Invalid("say who you are: `model` (and `shell`), or `agent_name` for a tool")
    device = new_token()
    code = DeviceCode(
        device_code_hash=hash_token(device),
        user_code=new_user_code(),
        agent_name=name,
        scopes=body.scopes,
        expires_at=now() + timedelta(minutes=DEVICE_TTL_MINUTES),
    )
    session.add(code)
    await session.commit()
    return DeviceStartOut(
        device_code=device,
        user_code=code.user_code,
        verification_uri=f"{get_settings().web_url}/device",
        expires_in=DEVICE_TTL_MINUTES * 60,
    )


class DeviceApproveIn(BaseModel):
    user_code: str


class DevicePreviewOut(BaseModel):
    agent_name: str
    scopes: list[str]
    expires_at: str


async def _pending_code(session: Session, user_code: str) -> DeviceCode:
    code = (
        await session.execute(
            select(DeviceCode).where(DeviceCode.user_code == user_code.strip().upper())
        )
    ).scalar_one_or_none()
    if code is None or code.expires_at < now() or code.approved_by is not None:
        raise NotFound("unknown or expired code")
    return code


@router.get("/device/{user_code}", response_model=DevicePreviewOut)
async def device_preview(user_code: str, user: User, session: Session) -> DevicePreviewOut:
    """What an agent asks for (its name and scopes), shown before the person approves."""
    if user.via != "session":
        raise Forbidden("approve device codes from the website")
    code = await _pending_code(session, user_code)
    return DevicePreviewOut(
        agent_name=code.agent_name, scopes=code.scopes, expires_at=code.expires_at.isoformat()
    )


@router.post("/device/approve", response_model=OkOut)
async def device_approve(body: DeviceApproveIn, user: User, session: Session) -> OkOut:
    if user.via != "session":
        raise Forbidden("approve device codes from the website")
    code = await _pending_code(session, body.user_code)
    token, plaintext = await create_token(session, user.operator, code.agent_name, code.scopes)
    code.approved_by = user.id
    code.token_id = token.id
    code.pending_token = plaintext
    audit.record(session, user.id, "device.approved", "token", token.id)
    await session.commit()
    return OkOut()


class DeviceTokenIn(BaseModel):
    device_code: str


class DeviceTokenOut(BaseModel):
    status: str
    access_token: str | None = None


@router.post("/device/token", response_model=DeviceTokenOut)
async def device_token(body: DeviceTokenIn, session: Session) -> DeviceTokenOut:
    code = (
        await session.execute(
            select(DeviceCode).where(DeviceCode.device_code_hash == hash_token(body.device_code))
        )
    ).scalar_one_or_none()
    if code is None or code.expires_at < now():
        return DeviceTokenOut(status="expired")
    if code.approved_by is None:
        return DeviceTokenOut(status="authorization_pending")
    token = code.pending_token
    if token is None:
        return DeviceTokenOut(status="already_issued")
    code.pending_token = None  # the plaintext token is handed out exactly once
    await session.commit()
    return DeviceTokenOut(status="ok", access_token=token)
