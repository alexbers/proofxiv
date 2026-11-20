"""Owner commands: authorized by the owner key's signature, so they work without an API token
(an agent setting up a fresh instance); `peer add --fingerprint` checks the lab's key itself."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from proofxiv_protocol.signing import generate_keypair, key_fingerprint
from proofxiv_worker import cli, config, owner
from typer.testing import CliRunner

LAB_PRIVATE, LAB_PUBLIC = generate_keypair()


@pytest.fixture
def sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict, dict]]:
    monkeypatch.setenv("PROOFXIV_HOME", str(tmp_path))
    private, _ = generate_keypair()
    key = tmp_path / "owner.key"
    key.write_text(private + "\n")
    key.chmod(0o600)
    config.save(config.Config(server="https://here.example", owner_key_file=str(key)))  # never signed in
    statements: list[tuple[dict, dict]] = []
    monkeypatch.setattr(
        owner, "send", lambda client, _key, **fields: statements.append((dict(client.http.headers), fields))
    )
    card = {"name": "Lab", "base_url": "https://lab.example", "owner_keys": [LAB_PUBLIC]}
    monkeypatch.setattr(
        httpx, "get", lambda url, timeout: httpx.Response(200, json=card, request=httpx.Request("GET", url))
    )
    return statements


def test_peer_add_with_fingerprint_needs_no_token(sent):
    result = CliRunner().invoke(
        cli.app, ["owner", "peer", "add", "https://lab.example", "--trust", "index", "--fingerprint", key_fingerprint(LAB_PUBLIC)]
    )
    assert result.exit_code == 0, result.output
    [(headers, fields)] = sent
    assert "authorization" not in {h.lower() for h in headers}
    assert fields == {"action": "add_peer", "peer_url": "https://lab.example", "peer_owner_key": LAB_PUBLIC, "trust": "index"}


def test_peer_add_refuses_another_fingerprint(sent):
    _, other = generate_keypair()
    result = CliRunner().invoke(
        cli.app, ["owner", "peer", "add", "https://lab.example", "--fingerprint", key_fingerprint(other)]
    )
    assert result.exit_code != 0
    assert "no owner key" in result.output
    assert sent == []


def test_attest_key_needs_no_token(sent):
    _, federation = generate_keypair()
    result = CliRunner().invoke(cli.app, ["owner", "attest-key", "--role", "federation", "--public-key", federation])
    assert result.exit_code == 0, result.output
    assert sent[0][1]["action"] == "attest_key"
