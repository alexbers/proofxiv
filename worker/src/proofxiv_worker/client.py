"""Thin HTTP client for the ProofXiv API."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx

from .config import Config


class ApiError(RuntimeError):
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload
        message = payload.get("message") if isinstance(payload, dict) else str(payload)
        super().__init__(f"HTTP {status}: {message or payload}")


class Client:
    def __init__(self, config: Config, token: str | None = None, transport: Any = None) -> None:
        token = token or config.token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(
            base_url=config.server.rstrip("/") + "/api/v1",
            headers=headers,
            timeout=httpx.Timeout(120.0, connect=15.0),
            transport=transport,
        )

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(self, method: str, path: str, **kw: Any) -> Any:
        resp = self.http.request(method, path, **kw)
        if resp.status_code == 204:
            return None
        try:
            payload = resp.json()
        except ValueError:
            payload = resp.text
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, payload)
        return payload

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.request("POST", path, **kw)

    def download(self, sha256: str, target: Path, base: Path | None = None) -> Path:
        """Fetch an artifact and verify its hash. With `base`, the target must lie inside it:
        file names come from submissions, and a name like '../../.bashrc' must not escape."""
        if base is not None and not target.resolve().is_relative_to(base.resolve()):
            raise ApiError(400, f"unsafe file path outside the work directory: {target}")
        # the site sends files from a separate domain; the hash check below makes that safe
        resp = self.http.get(f"/artifacts/{sha256}", follow_redirects=True)
        if resp.status_code != 200:
            raise ApiError(resp.status_code, resp.text)
        data = resp.content
        if hashlib.sha256(data).hexdigest() != sha256:
            raise RuntimeError(f"artifact {sha256} failed the hash check")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def upload(self, data: bytes, name: str, embargo: bool = False) -> str:
        """Store a file; an embargoed one (a held-out review transcript) stays private until
        the owner reveals the prompt."""
        out = self.post(
            "/artifacts",
            params={"embargo": "true"} if embargo else None,
            files={"file": (name, data, "application/octet-stream")},
        )
        return out["sha256"]
