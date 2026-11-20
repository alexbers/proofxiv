"""LLM harnesses: run an agent with a prompt in a workspace and capture the full transcript.

All model calls of the platform happen here, on the operator's machine and account.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import HarnessConfig

READ_TOOLS = ["Read", "Glob", "Grep"]
WEB_TOOLS = ["WebSearch", "WebFetch"]


@dataclass
class HarnessResult:
    output: dict[str, Any] | None
    text: str
    transcript: bytes
    vendor: str
    model: str
    harness: str
    request_ids: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    error: str | None = None


class HarnessError(RuntimeError):
    pass


def extract_json(text: str) -> dict[str, Any] | None:
    """Last JSON object in the text: a ```json block or the last balanced {...}."""
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidates = list(reversed(blocks))
    depth, start = 0, None
    spans: list[tuple[int, int]] = []
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                spans.append((start, i + 1))
    candidates += [text[a:b] for a, b in reversed(spans)]
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


class ClaudeCodeHarness:
    """Claude Code in headless mode: `claude -p --output-format stream-json --json-schema ...`."""

    name = "claude-code"

    def __init__(self, config: HarnessConfig) -> None:
        self.config = config
        self.vendor = config.vendor
        self.binary = shutil.which("claude")
        if self.binary is None:
            raise HarnessError("the `claude` CLI is not installed")
        version = subprocess.run([self.binary, "--version"], capture_output=True, text=True)
        self.version = version.stdout.strip()

    def run(
        self, prompt: str, schema: dict[str, Any], workspace: Path, allow_web: bool, timeout_s: float = 3600
    ) -> HarnessResult:
        tools = READ_TOOLS + (WEB_TOOLS if allow_web else [])
        cmd = [
            str(self.binary),
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--json-schema",
            json.dumps(schema),
            "--max-turns",
            str(self.config.max_turns),
            "--tools",
            ",".join(tools),
            "--allowedTools",
            ",".join(tools),
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps({"mcpServers": {}}),
        ]
        if self.config.isolated:
            # no personal CLAUDE.md, hooks, skills or plugins: the run must follow only the
            # official prompt, so that every trusted operator gets comparable results
            cmd.append("--safe-mode")
        if self.config.model:
            cmd += ["--model", self.config.model]
        if self.config.max_budget_usd:
            cmd += ["--max-budget-usd", str(self.config.max_budget_usd)]
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, cwd=workspace, timeout=timeout_s
        )
        transcript = proc.stdout.encode()
        model = self.config.model or "unknown"
        request_ids: list[str] = []
        final: dict[str, Any] = {}
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                model = event.get("model") or model
            elif event.get("type") == "assistant":
                message = event.get("message") or {}
                if message.get("id"):
                    request_ids.append(message["id"])
                model = message.get("model") or model
            elif event.get("type") == "result":
                final = event
        text = str(final.get("result") or "")
        output = final.get("structured_output")
        if not isinstance(output, dict):
            output = extract_json(text)
        error = None
        if proc.returncode != 0 or final.get("is_error"):
            error = f"claude exited with {proc.returncode}: {proc.stderr[-2000:]} {text[-1000:]}"
        return HarnessResult(
            output=output,
            text=text,
            transcript=transcript,
            vendor=self.vendor,
            model=model,
            harness=f"claude-code {self.version}",
            request_ids=sorted(set(request_ids)),
            cost_usd=final.get("total_cost_usd"),
            error=error,
        )


class CommandHarness:
    """Any CLI that reads the prompt on stdin and prints a JSON object as its last output."""

    name = "command"

    def __init__(self, config: HarnessConfig) -> None:
        if not config.command:
            raise HarnessError("command harness needs `command`")
        self.config = config
        self.vendor = config.vendor

    def run(
        self, prompt: str, schema: dict[str, Any], workspace: Path, allow_web: bool, timeout_s: float = 3600
    ) -> HarnessResult:
        full_prompt = prompt + "\n\nAnswer with one JSON object matching this schema:\n" + json.dumps(schema)
        proc = subprocess.run(
            self.config.command, input=full_prompt, capture_output=True, text=True, cwd=workspace, timeout=timeout_s
        )
        return HarnessResult(
            output=extract_json(proc.stdout),
            text=proc.stdout,
            transcript=(full_prompt + "\n---\n" + proc.stdout + "\n---\n" + proc.stderr).encode(),
            vendor=self.vendor,
            model=self.config.model or "unknown",
            harness=f"command {self.config.command[0]}",
            error=None if proc.returncode == 0 else f"exit {proc.returncode}: {proc.stderr[-2000:]}",
        )


class StubHarness:
    """Deterministic harness for tests: `responder(prompt, schema, workspace)` returns the output."""

    name = "stub"

    def __init__(self, vendor: str, responder: Callable[[str, dict, Path], dict[str, Any]]) -> None:
        self.vendor = vendor
        self.responder = responder

    def run(
        self, prompt: str, schema: dict[str, Any], workspace: Path, allow_web: bool, timeout_s: float = 3600
    ) -> HarnessResult:
        output = self.responder(prompt, schema, workspace)
        transcript = json.dumps({"prompt": prompt, "output": output}).encode()
        return HarnessResult(
            output=output,
            text=json.dumps(output),
            transcript=transcript,
            vendor=self.vendor,
            model="stub-model",
            harness="stub",
            request_ids=["stub-request"],
            cost_usd=0.0,
        )


def make(config: HarnessConfig):
    if config.type == "claude-code":
        return ClaudeCodeHarness(config)
    if config.type == "command":
        return CommandHarness(config)
    raise HarnessError(f"unknown harness type '{config.type}'")
