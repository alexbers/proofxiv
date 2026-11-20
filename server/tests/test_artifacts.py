"""Submitted files: a separate origin in production, header-safe names everywhere."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx


async def test_main_site_sends_files_to_their_own_origin(transport):
    from proofxiv_server.config import get_settings

    settings = get_settings()
    saved = settings.artifacts_url
    settings.artifacts_url = "https://files.example.org"
    sha = "a" * 64
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver/api/v1") as c:
            site = urlparse(settings.public_url).netloc
            on_site = await c.get(f"/artifacts/{sha}", params={"inline": "true"}, headers={"Host": site})
            internal = await c.get(f"/artifacts/{sha}")  # e.g. Next.js calling the API directly
    finally:
        settings.artifacts_url = saved
    assert on_site.status_code == 307
    assert on_site.headers["location"] == f"https://files.example.org/api/v1/artifacts/{sha}?inline=true"
    assert internal.status_code == 404  # served on this origin (no such file)


def test_download_names_cannot_break_headers():
    from proofxiv_server.api.routers.public import _disposition

    value = _disposition("attachment", 'отчёт "v2"\r\nSet-Cookie: x.pdf')
    assert "\r" not in value and "\n" not in value
    assert value.startswith('attachment; filename="') and "filename*=UTF-8''" in value
    assert _disposition("inline", "paper.pdf").startswith('inline; filename="paper.pdf"')
