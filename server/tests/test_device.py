"""Device sign-in for agents: the agent shows a code, the person approves it on the website."""

from __future__ import annotations

from .conftest import Api, login


async def test_device_flow(transport):
    anon = Api(transport)
    start = await anon.post("/auth/device/code", json={"agent_name": "laptop-agent", "scopes": ["agent"]})
    pending = await anon.post("/auth/device/token", json={"device_code": start["device_code"]})
    assert pending["status"] == "authorization_pending"

    dave = await login(transport, "dave")
    # the person sees what the agent asks for before approving
    await anon.get(f"/auth/device/{start['user_code']}", expect=401)
    preview = await dave.get(f"/auth/device/{start['user_code'].lower()}")
    assert preview["agent_name"] == "laptop-agent" and preview["scopes"] == ["agent"]
    await dave.post("/auth/device/approve", json={"user_code": start["user_code"].lower()})
    await dave.get(f"/auth/device/{start['user_code']}", expect=404)  # already approved
    issued = await anon.post("/auth/device/token", json={"device_code": start["device_code"]})
    assert issued["status"] == "ok" and issued["access_token"].startswith("pxv_")
    again = await anon.post("/auth/device/token", json={"device_code": start["device_code"]})
    assert again["status"] == "already_issued"

    agent = Api(transport, issued["access_token"])
    me = await agent.get("/me")
    assert me["login"] == "dave"
    # tokens cannot approve device codes or mint new tokens: only the website session can
    await agent.post("/auth/device/approve", json={"user_code": "ABCD-EFGH"}, expect=403)
    await agent.post("/me/tokens", json={"agent_name": "x"}, expect=403)


def test_sign_in_returns_only_to_local_pages():
    from proofxiv_server.api.routers.auth import safe_next

    assert safe_next("/papers/42?tab=reviews") == "/papers/42?tab=reviews"
    for bad in (None, "", "https://evil.example/", "//evil.example/x", "/\\evil", "/a\r\nSet-Cookie: x"):
        assert safe_next(bad) == "/"


async def test_root_of_trust(transport):
    from proofxiv_protocol.signing import key_fingerprint

    from .conftest import OWNER_PUBLIC

    trust = await Api(transport).get("/root-of-trust")
    assert trust["keys"] == [{"public_key": OWNER_PUBLIC, "fingerprint": key_fingerprint(OWNER_PUBLIC)}]
    assert trust["logins"] == ["owner"]


async def test_agent_onboarding_docs(transport):
    import httpx

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        start = await c.get("/start.md")
        skill = await c.get("/SKILL.md")
    assert start.status_code == 200 and "/api/v1/auth/device/code" in start.text
    assert "{BASE}" not in start.text and "{BASE}" not in skill.text
    assert skill.text.startswith("---\nname: proofxiv")
