"""Test setup: a real PostgreSQL database (scripts/dev_pg.sh) and the ASGI app in-process."""

from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from proofxiv_protocol.owner import OwnerStatement
from proofxiv_protocol.signing import SignedBundle, generate_keypair, new_nonce, utc_now_iso

ROOT = Path(__file__).resolve().parents[2]
TEST_DATA = ROOT / ".dev" / "test-data"
OWNER_PRIVATE, OWNER_PUBLIC = generate_keypair()

os.environ["PROOFXIV_DATABASE_URL"] = os.environ.get(
    "PROOFXIV_TEST_DATABASE_URL", "postgresql+asyncpg://proofxiv@127.0.0.1:54329/proofxiv_test"
)
os.environ["PROOFXIV_DEV_MODE"] = "1"
os.environ["PROOFXIV_DATA_DIR"] = str(TEST_DATA)
os.environ["PROOFXIV_OWNER_PUBLIC_KEYS"] = json.dumps([OWNER_PUBLIC])
os.environ["PROOFXIV_OWNER_LOGINS"] = json.dumps(["owner"])
os.environ["PROOFXIV_SESSION_SECRET"] = "test-secret-test-secret-test-secret-0123"
os.environ["PROOFXIV_PUBLIC_CACHE_SECONDS"] = "0"


@pytest.fixture(scope="session")
async def app():
    from proofxiv_server.config import get_settings
    from proofxiv_server.db.session import create_schema, init_engine
    from proofxiv_server.main import create_app

    get_settings.cache_clear()
    shutil.rmtree(TEST_DATA, ignore_errors=True)
    engine = init_engine(os.environ["PROOFXIV_DATABASE_URL"])
    await create_schema(engine, drop=True)
    yield create_app()
    await engine.dispose()


@pytest.fixture(scope="session")
def transport(app):
    return httpx.ASGITransport(app=app)


class Api:
    """A tiny client wrapper: one per actor (cookie session or bearer token)."""

    def __init__(self, transport: httpx.ASGITransport, token: str | None = None) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.AsyncClient(
            transport=transport, base_url="http://testserver/api/v1", headers=headers
        )

    async def call(self, method: str, path: str, expect: int = 200, **kw: Any) -> Any:
        resp = await self.client.request(method, path, **kw)
        assert resp.status_code == expect, f"{method} {path}: {resp.status_code} {resp.text}"
        if resp.status_code == 204 or not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        return resp.json() if "json" in ctype else resp.text

    async def get(self, path: str, expect: int = 200, **kw: Any) -> Any:
        return await self.call("GET", path, expect, **kw)

    async def post(self, path: str, expect: int = 200, **kw: Any) -> Any:
        return await self.call("POST", path, expect, **kw)


async def login(transport: httpx.ASGITransport, name: str) -> Api:
    api = Api(transport)
    await api.post("/auth/dev-login", json={"login": name})
    return api


async def agent(
    transport: httpx.ASGITransport, web: Api, name: str, scopes: list[str] | None = None
) -> Api:
    token = await web.post(
        "/me/tokens", json={"agent_name": name, "scopes": scopes or ["agent"]}
    )
    return Api(transport, token["token"])


def owner_statement(**fields: Any) -> dict:
    statement = OwnerStatement(issued_at=utc_now_iso(), nonce=new_nonce(), **fields)
    return SignedBundle.create(statement, OWNER_PRIVATE).model_dump()


def make_bundle(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(name=f"paper/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()
