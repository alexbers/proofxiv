"""Worker against a real server process: submit, Lean in the sandbox, exchange, weekly run (stub model)."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from proofxiv_protocol.signing import generate_keypair

ROOT = Path(__file__).resolve().parents[2]
VENV = ROOT / ".venv" / "bin"
TOOLCHAIN = ROOT.parent / "attempts" / "toolchain" / "lean-4.34.1-linux"
HOME = ROOT / ".dev" / "worker-test-home"
DB = "postgresql+asyncpg://proofxiv@127.0.0.1:54329/proofxiv_worker_test"
OWNER_PRIVATE, OWNER_PUBLIC = generate_keypair()

pytestmark = pytest.mark.skipif(
    not (TOOLCHAIN / "bin" / "lean").exists() or shutil.which("bwrap") is None,
    reason="needs the local Lean toolchain and bubblewrap",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url():
    port = _free_port()
    env = {
        **os.environ,
        "PROOFXIV_DATABASE_URL": DB,
        "PROOFXIV_DEV_MODE": "1",
        "PROOFXIV_DATA_DIR": str(ROOT / ".dev" / "worker-test-data"),
        "PROOFXIV_OWNER_PUBLIC_KEYS": json.dumps([OWNER_PUBLIC]),
        "PROOFXIV_OWNER_LOGINS": json.dumps(["owner"]),
        "PROOFXIV_SESSION_SECRET": "worker-test-secret-worker-test-secret-012345",
        "PROOFXIV_PUBLIC_URL": f"http://127.0.0.1:{port}",
    }
    subprocess.run(
        ["psql", "-h", "127.0.0.1", "-p", "54329", "-U", "proofxiv", "-d", "postgres", "-qc",
         "CREATE DATABASE proofxiv_worker_test"],
        capture_output=True,
    )
    subprocess.run([VENV / "proofxiv-server", "create-schema", "--drop"], env=env, check=True)
    log = (ROOT / ".dev" / "worker-test-server.log").open("w")
    proc = subprocess.Popen(
        [VENV / "proofxiv-server", "serve", "--port", str(port)], env=env, stdout=log, stderr=log
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(url + "/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    yield url
    proc.terminate()
    proc.wait(timeout=10)


def _token(url: str, login: str, scopes: list[str]) -> str:
    with httpx.Client(base_url=url + "/api/v1") as web:
        web.post("/auth/dev-login", json={"login": login}).raise_for_status()
        resp = web.post("/me/tokens", json={"agent_name": f"{login}-agent", "scopes": scopes})
        resp.raise_for_status()
        return resp.json()["token"]


def _paper(folder: Path, title: str, lean: str, theorems: list[str]) -> None:
    (folder / "lean").mkdir(parents=True, exist_ok=True)
    (folder / "lean" / "Main.lean").write_text(lean)
    (folder / "paper.pdf").write_bytes(b"%PDF-1.4 " + title.encode())
    (folder / "REVIEW.md").write_text("# Review\nIndependent check: the construction is fine.")
    (folder / "paper.json").write_text(
        json.dumps(
            {
                "title": title,
                "abstract": "A small certified statement used to test the pipeline.",
                "byline": "Claude (AI model by Anthropic) — run by alice",
                "claims": [
                    {
                        "key": "main",
                        "kind": "lean",
                        "statement": "The arithmetic identity.",
                        "lean_file": "lean/Main.lean",
                        "lean_theorems": theorems,
                    }
                ],
                "novelty": {"what_is_new": "Nothing; this is a pipeline test."},
                "files": {"pdf": "paper.pdf"},
                "author_reviews": [{"file": "REVIEW.md", "model": "claude"}],
                "lean_toolchain": "leanprover/lean4:v4.34.1",
            }
        )
    )


def _draft(materials: dict) -> dict:
    responses = []
    for review in materials.get("paper", {}).get("reviews", []):
        for i, issue in enumerate((review.get("body") or {}).get("issues", [])):
            if issue["severity"] in ("critical", "major"):
                responses.append(
                    {"review_id": review["id"], "issue_index": i, "verdict": "accepted", "reasoning": "fine"}
                )
    return {
        "summary": "The paper states an arithmetic identity and certifies it in Lean; the statement "
        "is faithful and the kernel accepted it with no axioms.",
        "correctness": "Kernel-checked.",
        "statement_faithfulness": "Faithful.",
        "novelty": {"summary": "Not new; test.", "category": "new"},
        "significance": "None (test).",
        "issues": [],
        "responses": responses,
        "recommendation": "accept",
        "confidence": 5,
    }


def responder(prompt: str, schema: dict, ws: Path) -> dict:
    kind = prompt.split("- kind: ", 1)[1].split()[0] if "- kind: " in prompt else "exchange"
    materials = json.loads((ws / "materials.json").read_text())
    if kind in ("novelty_consistency", "novelty_audit", "anchor_review", "statement_audit", "heldout_review"):
        return {"review": _draft(materials), "novelty_category": "new"}
    if kind == "exchange":
        return _draft({})
    if kind in ("confirm_problem", "confirm_event"):
        return {"confirm": {"confirmed": True, "reasoning": "Checked the sources (stub)."}}
    if kind == "sync":
        return {"sync": {"problem_slugs": [materials["problem"]["slug"]], "sources_checked": ["stub"], "events": []}}
    if kind == "digest":
        return {"digest_md": "# Week\nOne paper verified by the platform (stub digest)."}
    if kind == "moderation":
        return {"upheld": False, "action": "none", "reasoning": "Report not supported (stub)."}
    raise AssertionError(kind)


def fake_web(url: str, params: dict[str, str]) -> tuple[int, bytes]:
    """arXiv knows one paper; nothing else is reachable."""
    if "id_list" in params:
        entry = "<entry><id>http://arxiv.org/abs/2301.00001v2</id><title>A certified identity</title></entry>"
        return 200, f'<feed xmlns="http://www.w3.org/2005/Atom">{entry}</feed>'.encode()
    return 404, b""


def test_worker_end_to_end(server_url, monkeypatch):
    shutil.rmtree(HOME, ignore_errors=True)
    monkeypatch.setenv("PROOFXIV_HOME", str(HOME))
    from proofxiv_worker import bundle, keys, modes, owner, references, runner, weekly
    from proofxiv_worker.client import Client
    from proofxiv_worker.config import Config
    from proofxiv_worker.harness import StubHarness

    cfg = Config(
        server=server_url,
        token=_token(server_url, "owner", ["agent", "runner", "trusted"]),
        sandbox="bwrap",
        lean_toolchains={"4.34.1": str(TOOLCHAIN)},
    )
    trusted = Client(cfg)
    alice = Client(cfg.model_copy(update={"token": _token(server_url, "alice", ["agent"])}))
    bob = Client(cfg.model_copy(update={"token": _token(server_url, "bob", ["agent"])}))

    for role in ("reviewer", "runner"):
        owner.send(trusted, OWNER_PRIVATE, action="grant_role", operator_login="owner", role=role)
    assert len(owner.approve_prompts(trusted, OWNER_PRIVATE)) == 10
    assert owner.approve_prompts(trusted, OWNER_PRIVATE) == []  # nothing changed
    # a held-out prompt: the server gets the hash, the text stays in $PROOFXIV_HOME/heldout
    version, heldout_file = owner.commit_heldout(trusted, OWNER_PRIVATE, "Check the arithmetic twice.")
    assert version == 1 and heldout_file.read_text().endswith("Check the arithmetic twice.\n")
    listed = trusted.get("/prompts/heldout_review/1")
    assert listed["text"] is None and listed["held_out"]
    path, public, _ = keys.new_key("weekly")
    trusted.post("/me/keys", json={"public_key": public, "label": "weekly"})
    private = keys.load_private(path)

    good, bad = HOME / "good", HOME / "bad"
    _paper(good, "A certified identity", "theorem two : 1 + 1 = 2 := by decide\n", ["two"])
    _paper(bad, "A false identity", "theorem three : 1 + 1 = 3 := by decide\n", ["three"])
    (good / "refs.bib").write_text("@misc{self, title = {A certified identity}, eprint = {2301.00001}}\n")
    monkeypatch.setattr(references, "http_fetch", lambda contact=None: fake_web)
    ids = {}
    for name, folder in (("good", good), ("bad", bad)):
        _, data, _ = bundle.pack(folder)
        out = alice.post("/papers", files={"bundle": ("p.tar.gz", data, "application/gzip")})
        ids[name] = out["paper_id"]
        assert out["level"] == "C1" and "author_reviews_only" in out["labels"]

    # machine checks: real Lean inside bubblewrap; the bibliography against a fake arXiv
    assert runner.loop(trusted, cfg, private, log=print) == 3
    good_page = alice.get(f"/papers/{ids['good']}")
    bad_page = alice.get(f"/papers/{ids['bad']}")
    assert good_page["level"] == "C2", good_page["claims"]
    assert good_page["claims"][0]["runs"][0]["lean"]["theorems"] == {"two": []}
    assert good_page["references"]["counts"]["verified"] == 1 and "references_verified" in good_page["labels"]
    assert "machine_check_failed" in bad_page["labels"]
    assert bad_page["claims"][0]["runs"][0]["verdict"] == "fail"

    # review exchange with a stub model
    alice.post(f"/papers/{ids['good']}/review-requests", json={"count": 1})
    assert modes.exchange_reviews(bob, StubHarness("anthropic", responder), 1, [], print) == 1
    assert bob.get("/exchange/balance")["balance"] == 2

    # the weekly trusted run (stub model, real signatures)
    stats = weekly.weekly(
        trusted, cfg, private, {"anthropic": StubHarness("anthropic", responder)}, with_runner=False, log=print
    )
    assert stats.tasks_failed == 0, stats.failures
    good_page = alice.get(f"/papers/{ids['good']}")
    assert good_page["level"] == "C3" and "platform_verified" in good_page["labels"]
    trusted_reviews = [r for r in good_page["reviews"] if r["origin"] == "trusted"]
    assert trusted_reviews and trusted_reviews[0]["signed"] and trusted_reviews[0]["transcript_sha256"]
    # the held-out review ran with the local text; its transcript is private until the reveal
    heldout = next(r for r in trusted_reviews if r["kind"] == "heldout_review")
    assert heldout["transcript_embargoed"]
    transcript_url = f"{server_url}/api/v1/artifacts/{heldout['transcript_sha256']}"
    assert httpx.get(transcript_url).status_code == 403
    owner.reveal_heldout(trusted, OWNER_PRIVATE, version)
    revealed = httpx.get(transcript_url)
    assert revealed.status_code == 200 and "Check the arithmetic twice." in revealed.text
    assert trusted.get("/prompts/heldout_review/1")["text"] == heldout_file.read_text()
    assert alice.get("/digests")[0]["digest_md"].startswith("# Week")


def test_register_and_sign_in_links(server_url, monkeypatch):
    """`proofxiv register` without GitHub, then one-time website links for the person and the owner."""
    from proofxiv_worker import cli, config, owner
    from proofxiv_worker.client import Client
    from typer.testing import CliRunner

    frank_home = HOME / "frank"
    shutil.rmtree(frank_home, ignore_errors=True)
    monkeypatch.setenv("PROOFXIV_HOME", str(frank_home))
    result = CliRunner().invoke(cli.app, ["register", "--login", "frank", "--server", server_url, "--model", "Claude Fable 5.2", "--shell", "Claude Code"])
    assert result.exit_code == 0, result.output
    assert "/sign-in/" in result.output and "Claude Fable 5.2 (Claude Code)" in result.output
    assert (frank_home / "token").stat().st_mode & 0o777 == 0o600
    cfg = config.load()
    with Client(cfg) as client:
        assert client.get("/me")["login"] == "frank"
    link = CliRunner().invoke(cli.app, ["sign-in-link"])
    assert link.exit_code == 0 and "/sign-in/" in link.output

    with Client(cfg.model_copy(update={"token": None}), token="") as anon:
        url = owner.sign_in_link(anon, OWNER_PRIVATE, "owner")
    code = url.rsplit("/sign-in/", 1)[1]
    with httpx.Client(base_url=server_url + "/api/v1") as browser:
        assert browser.get(f"/auth/sign-in-link/{code}").json()["login"] == "owner"
        browser.post(f"/auth/sign-in-link/{code}").raise_for_status()
        assert browser.get("/me").json()["is_owner"]
