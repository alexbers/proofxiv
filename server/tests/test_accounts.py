"""Accounts without GitHub: the agent registers, the person signs in with a one-time link."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from proofxiv_protocol.owner import OwnerSignIn
from proofxiv_protocol.signing import SignedBundle, generate_keypair, new_nonce, utc_now_iso

from .conftest import OWNER_PRIVATE, Api, login


def code_of(url: str) -> str:
    assert "/sign-in/" in url, url
    return url.rsplit("/sign-in/", 1)[1]


async def test_agent_registers_and_hands_over_a_link(transport):
    anon = Api(transport)
    assert await anon.get("/auth/methods") == {"github": False, "registration": True, "dev_login": True}

    out = await anon.post("/auth/register", json={"login": "Dana", "model": "Claude Fable 5.2", "shell": "Claude Code"})
    assert out["agent_name"] == "Claude Fable 5.2 (Claude Code)"
    assert out["login"] == "dana" and out["access_token"].startswith("pxv_")
    agent = Api(transport, out["access_token"])
    me = await agent.get("/me")
    assert me["login"] == "dana"

    await anon.post("/auth/register", json={"login": "DANA", "model": "Claude Fable 5.2", "shell": "Claude Code"}, expect=409)  # taken, any case
    await anon.post("/auth/register", json={"login": "owner", "model": "Claude Fable 5.2", "shell": "Claude Code"}, expect=403)  # an owner login
    await anon.post("/auth/register", json={"login": "admin", "model": "Claude Fable 5.2", "shell": "Claude Code"}, expect=403)
    await anon.post("/auth/register", json={"login": "bad name", "model": "Claude Fable 5.2", "shell": "Claude Code"}, expect=422)

    # the person: the link page shows whom it signs in, then the button uses it once
    person = Api(transport)
    code = code_of(out["sign_in"]["url"])
    preview = await person.get(f"/auth/sign-in-link/{code}")
    assert preview["login"] == "dana"
    signed = await person.post(f"/auth/sign-in-link/{code}")
    assert signed["login"] == "dana"
    assert (await person.get("/me"))["login"] == "dana"  # the session cookie is set
    await Api(transport).post(f"/auth/sign-in-link/{code}", expect=404)  # used up
    await person.get(f"/auth/sign-in-link/{code}", expect=404)

    # later: the agent makes a fresh link with its token
    fresh = await agent.post("/auth/sign-in-link")
    again = Api(transport)
    await again.post(f"/auth/sign-in-link/{code_of(fresh['url'])}")
    assert (await again.get("/me"))["login"] == "dana"
    await Api(transport).post("/auth/sign-in-link", expect=401)


async def test_owner_signs_in_with_the_owner_key(transport):
    def request(key: str, login: str = "owner", issued: str | None = None) -> dict:
        payload = OwnerSignIn(login=login, issued_at=issued or utc_now_iso(), nonce=new_nonce())
        return SignedBundle.create(payload, key).model_dump()

    anon = Api(transport)
    link = await anon.post("/auth/owner-sign-in", json=request(OWNER_PRIVATE))
    owner = Api(transport)
    await owner.post(f"/auth/sign-in-link/{code_of(link['url'])}")
    me = await owner.get("/me")
    assert me["login"] == "owner" and me["is_owner"]

    stranger, _ = generate_keypair()
    await anon.post("/auth/owner-sign-in", json=request(stranger), expect=403)
    await anon.post("/auth/owner-sign-in", json=request(OWNER_PRIVATE, login="dana"), expect=403)
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await anon.post("/auth/owner-sign-in", json=request(OWNER_PRIVATE, issued=old), expect=422)


async def test_registrations_are_limited_per_address(transport, monkeypatch):
    from proofxiv_server.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_registrations_per_hour", 2)
    anon = Api(transport)
    headers = {"x-forwarded-for": "203.0.113.9"}
    statuses = []
    for i in range(3):
        resp = await anon.client.post("/auth/register", json={"login": f"spam-{i}", "model": "Claude Fable 5.2", "shell": "Claude Code"}, headers=headers)
        statuses.append(resp.status_code)
    assert statuses == [200, 200, 429]


async def test_github_user_does_not_take_a_registered_name(transport):
    from proofxiv_server.core.identity import get_or_create_operator
    from proofxiv_server.db.session import sessionmaker

    await Api(transport).post("/auth/register", json={"login": "erik", "model": "Claude Fable 5.2", "shell": "Claude Code"})
    async with sessionmaker()() as s:
        github_erik = await get_or_create_operator(s, "erik", github_id=4242)
        assert github_erik.login == "erik-gh"
        await s.rollback()


async def test_agents_are_named_by_model_and_shell(transport):
    anon = Api(transport)
    anon.client.headers["x-forwarded-for"] = "203.0.113.50"  # its own registration allowance
    # the two ways agents got it wrong: the program instead of the model, the account instead of the model
    for who in ({"model": "claude-code"}, {"model": "Claude Code", "shell": "Claude Code"}, {"model": "gina"}, {}):
        resp = await anon.client.post("/auth/register", json={"login": "gina", **who})
        assert resp.status_code == 422, (who, resp.text)
    assert "program you run in" in (
        await anon.client.post("/auth/register", json={"login": "gina", "model": "claude-code"})
    ).json()["message"]

    out = await anon.post("/auth/register", json={"login": "gina", "model": " Claude  Opus 5.5 ", "shell": "Claude Code"})
    assert out["agent_name"] == "Claude Opus 5.5 (Claude Code)"
    agent = Api(transport, out["access_token"])
    assert (await agent.get("/me"))["agent_name"] == "Claude Opus 5.5 (Claude Code)"

    # another model picks up the saved token: its work goes under its own name, earlier work stays
    problem = await agent.post(
        "/problems",
        json={
            "title": "A test problem about who did what",
            "statement_md": "Which agent recorded which attempt on this problem?",
            "domain": "combinatorics",
            "sources": [{"url": "https://example.org/who"}],
            "openness_evidence": "Open by construction of the test.",
        },
    )
    slug = problem["problem"]["slug"]
    await agent.post(f"/problems/{slug}/attempts", json={"outcome": "note", "notes_md": "First attempt, by Opus."})
    declared = await agent.post("/me/agent", json={"model": "Claude Fable 5.2", "shell": "Claude Code"})
    assert declared == {"login": "gina", "agent_name": "Claude Fable 5.2 (Claude Code)"}
    await agent.post(f"/problems/{slug}/attempts", json={"outcome": "note", "notes_md": "Second attempt, by Fable."})
    attempts = (await agent.get(f"/problems/{slug}"))["attempts"]
    assert sorted(a["agent_name"] for a in attempts) == [
        "Claude Fable 5.2 (Claude Code)",
        "Claude Opus 5.5 (Claude Code)",
    ]
    await agent.post("/me/agent", json={"model": "codex"}, expect=422)
    await (await login(transport, "gina-web")).post("/me/agent", json={"model": "Claude Fable 5.2"}, expect=403)

    # the device flow builds the same name; a tool without a model may still give its own
    start = await anon.post("/auth/device/code", json={"model": "GPT-5.5", "shell": "Codex CLI"})
    web = await login(transport, "gina-web")
    assert (await web.get(f"/auth/device/{start['user_code']}"))["agent_name"] == "GPT-5.5 (Codex CLI)"
    await anon.post("/auth/device/code", json={"scopes": ["agent"]}, expect=422)
