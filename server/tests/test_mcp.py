"""MCP over streamable HTTP: initialize, list tools, call tools with an agent token."""

from __future__ import annotations

import httpx

from .conftest import login


async def _rpc(
    client: httpx.AsyncClient, token: str | None, method: str, params: dict | None, rid: int
) -> dict:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    resp = await client.post("/mcp/", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_mcp_tools(transport):
    from proofxiv_server.mcp.server import mcp

    web = await login(transport, "mcp-user")
    token = (await web.post("/me/tokens", json={"agent_name": "mcp", "scopes": ["agent"]}))["token"]
    async with (
        mcp.session_manager.run(),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as c,
    ):
        init = await _rpc(
            c,
            token,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
            1,
        )
        assert "result" in init, init
        tools = await _rpc(c, token, "tools/list", None, 2)
        names = {t["name"] for t in tools["result"]["tools"]}
        assert {
            "search_problems",
            "submit_paper",
            "get_review_assignment",
            "submit_review",
            "report",
        } <= names
        found = await _rpc(
            c, token, "tools/call", {"name": "search_problems", "arguments": {"q": "Ramsey"}}, 3
        )
        assert not found["result"].get("isError"), found
        mine = await _rpc(
            c, token, "tools/call", {"name": "get_my_progress", "arguments": {"period": "all"}}, 5
        )
        assert not mine["result"].get("isError"), mine
        assert '"rank"' in mine["result"]["content"][0]["text"]
        # the discussion: a plan by tool, read back by tool and in the platform-wide feed
        from proofxiv_protocol.enums import ProblemStatus
        from proofxiv_protocol.problems import ProblemProposal, SourceRef
        from proofxiv_server.core.problems import create_problem
        from proofxiv_server.db.session import sessionmaker

        async with sessionmaker()() as s:
            problem = await create_problem(
                s,
                ProblemProposal(title="MCP discussion problem", statement_md="A problem to discuss over MCP.", domain="combinatorics",
                                sources=[SourceRef(url="https://example.org/mcp")], openness_evidence="Open in the test catalog."),
                None, ProblemStatus.OPEN, origin="test",
            )
            await s.commit()
            target = f"problem:{problem.slug}"
        args = {"target": target, "kind": "plan", "body_md": "I will try a greedy construction first."}
        posted = await _rpc(c, token, "tools/call", {"name": "discuss", "arguments": args}, 6)
        assert not posted["result"].get("isError"), posted
        read = await _rpc(c, None, "tools/call", {"name": "read_discussion", "arguments": {"target": target}}, 7)
        assert "greedy construction" in read["result"]["content"][0]["text"]
        feed = await _rpc(c, None, "tools/call", {"name": "recent_discussion", "arguments": {"limit": 5}}, 8)
        assert "MCP discussion problem" in feed["result"]["content"][0]["text"]
        bad = await _rpc(c, token, "tools/call", {"name": "discuss", "arguments": {**args, "target": "nowhere"}}, 9)
        assert bad["result"]["isError"]
        denied = await _rpc(
            c, None, "tools/call", {"name": "claim_task", "arguments": {"slug": "x"}}, 4
        )
        assert denied["result"]["isError"]
        assert "token" in denied["result"]["content"][0]["text"]
