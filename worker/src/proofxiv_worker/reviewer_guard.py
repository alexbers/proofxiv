"""PreToolUse hook for the `proofxiv-reviewer` Claude Code subagent (the weekly run done by
subagents): the reviewer reads and writes only inside its task's workspace, runs programs only in
the sandbox, and may use the web. It never sees the keys, the rest of the machine or the other
tasks (a held-out prompt, another task's answer).

A subagent is tied to the first task workspace it touches: the pin (agent id -> workspace) is kept
next to the workspaces' root, out of the subagent's reach.

Claude Code calls this with the tool call as JSON on stdin; exit code 2 blocks the call and tells
the subagent why. Anything unexpected blocks too: a guard that fails must not fail open.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
from pathlib import Path

AGENT = "proofxiv-reviewer"
FILE_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep"}
SHELL_TOOLS = {"Bash", "Monitor", "PowerShell"}
WEB_TOOLS = {"WebSearch", "WebFetch"}
REPLY_TOOLS = {"SubagentHandback"}  # only returns the subagent's report to the main session
SHELL_OPERATORS = set(";&|<>()")


def pins_dir(root: Path) -> Path:
    return root.parent / ".reviewer-guard"


def task_workspace(path: str, root: Path) -> Path | None:
    """The task workspace (<root>/<run>/<task>, with spec.json) that holds path, if any."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_relative_to(root):
        return None
    parts = resolved.relative_to(root).parts
    if len(parts) < 2:
        return None
    workspace = root / parts[0] / parts[1]
    return workspace if (workspace / "spec.json").is_file() else None


def pinned(workspace: Path, agent: str, root: Path) -> Path:
    """The workspace this subagent is tied to: the first one it touched."""
    pins = pins_dir(root)
    pins.mkdir(mode=0o700, parents=True, exist_ok=True)
    pin = pins / hashlib.sha256(agent.encode()).hexdigest()[:32]
    if not pin.exists():
        draft = pins / f".{pin.name}.{os.getpid()}"
        draft.write_text(str(workspace))
        try:
            os.link(draft, pin)  # atomic: of two first calls at once, one pin wins
        except FileExistsError:
            pass
        finally:
            draft.unlink()
    return Path(pin.read_text())


def confine(path: str, root: Path, agent: str) -> str | None:
    workspace = task_workspace(path, root)
    if workspace is None:
        return f"local files: only inside your task's workspace (a task directory under {root})"
    mine = pinned(workspace, agent, root)
    if workspace != mine:
        return f"local files: only inside your task's workspace ({mine})"
    return None


def check_command(command: str, root: Path, cli: Path, agent: str) -> str | None:
    """None when the command is `<cli> sandbox <workspace> [options] -- ...` or
    `<cli> check-output <workspace>` for the subagent's own workspace, written plainly (no
    chaining, substitution or redirects)."""
    if any(mark in command for mark in ("\n", "`", "$(", "${")):
        return "no newlines, backticks or $(...) in commands"
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        return f"cannot parse the command: {exc}"
    if any(token and set(token) <= SHELL_OPERATORS for token in tokens):
        return "one plain command only: no ;, &&, |, redirects or subshells"
    if len(tokens) < 3 or Path(tokens[0]).resolve() != cli.resolve():
        return f"run programs only through the sandbox: {cli} sandbox <workspace> -- <command>"
    action, workspace = tokens[1], tokens[2]
    if action not in ("sandbox", "check-output"):
        return f"allowed: {cli} sandbox ... and {cli} check-output <workspace>"
    if task_workspace(workspace, root) != Path(workspace).expanduser().resolve():
        return "the workspace must be your task's workspace"
    if problem := confine(workspace, root, agent):
        return problem
    if action == "sandbox" and "--" not in tokens[3:]:
        return "give the command after --"
    return None


def decide(event: dict, root: Path, cli: Path) -> str | None:
    """The reason to block the call, or None to let the usual permission rules decide."""
    if event.get("agent_type") != AGENT:
        return None
    tool = str(event.get("tool_name", ""))
    args = event.get("tool_input") or {}
    if tool in WEB_TOOLS or tool in REPLY_TOOLS:
        return None
    agent = str(event.get("agent_id") or event.get("session_id") or "")
    if not agent:
        return "cannot tell which subagent is calling"
    if tool in FILE_TOOLS:
        path = args.get("file_path") or args.get("notebook_path") or args.get("path")
        if not path:
            return "give an absolute path inside your workspace"
        pattern = str(args.get("pattern") or "")
        if tool == "Glob" and (pattern.startswith(("/", "~")) or ".." in Path(pattern).parts):
            return "glob patterns must be relative to your workspace"
        return confine(str(path), root, agent)
    if tool in SHELL_TOOLS:
        if tool == "Monitor" or args.get("run_in_background"):
            # a sandboxed program left running could swap a scratch/ symlink under the file checks
            return "no background commands"
        return check_command(str(args.get("command", "")), root, cli, agent)
    return f"{tool} is not available to reviewers"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="the directory with the task workspaces")
    parser.add_argument("--cli", required=True, help="the proofxiv command (absolute path)")
    try:
        args = parser.parse_args()
        event = json.loads(sys.stdin.read() or "{}")
        reason = decide(event, Path(args.root).resolve(), Path(args.cli))
    except Exception as exc:  # noqa: BLE001 - fail closed on anything unexpected
        reason = f"the reviewer guard failed ({exc}); call blocked"
    if reason:
        print(f"Blocked by the ProofXiv reviewer guard: {reason}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
