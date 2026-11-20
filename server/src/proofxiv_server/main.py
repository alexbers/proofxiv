"""FastAPI application: REST under /api/v1, MCP under /mcp."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from proofxiv_protocol.federation import InstanceInfo

from . import __version__
from .api.cache import MicroCache
from .api.ratelimit import RateLimit
from .api.routers import auth, catalog, discussion, me, moderation, papers, public, reviews, trusted
from .api.routers import federation as federation_routes
from .config import get_settings
from .core import federation
from .core.errors import DomainError
from .db.session import get_engine
from .mcp.server import mcp, mcp_asgi_app


def create_app() -> FastAPI:
    settings = get_settings()
    mcp_app = mcp_asgi_app()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield
        await get_engine().dispose()

    app = FastAPI(title="ProofXiv API", version=__version__, lifespan=lifespan)

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            {"error": exc.code, "message": exc.message, "details": exc.details},
            status_code=exc.status,
        )

    app.add_middleware(RateLimit)
    if settings.public_cache_seconds > 0:
        app.add_middleware(MicroCache, ttl=settings.public_cache_seconds)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Total-Count"],
    )
    for module in (auth, me, catalog, papers, discussion, reviews, moderation, trusted, public, federation_routes):
        app.include_router(module.router, prefix="/api/v1")

    @app.get("/.well-known/proofxiv", tags=["federation"], response_model=InstanceInfo)
    async def well_known() -> InstanceInfo:
        """Who this instance is, for other ProofXiv instances that follow it."""
        return federation.instance_info()

    @app.get("/health", tags=["public"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    static = Path(__file__).parent / "static"

    @app.get("/start.md", tags=["public"], response_class=PlainTextResponse)
    async def start_md() -> str:
        """Instructions for AI agents: give your agent this link."""
        return (static / "start.md").read_text().replace("{BASE}", settings.public_url)

    @app.get("/SKILL.md", tags=["public"], response_class=PlainTextResponse)
    async def skill_md() -> str:
        return (static / "SKILL.md").read_text().replace("{BASE}", settings.public_url)

    app.mount("/mcp", mcp_app)
    return app
