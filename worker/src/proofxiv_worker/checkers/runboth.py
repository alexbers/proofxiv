"""Run the Python and the Rust implementation on one certificate file and compare them."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_EXPECTED_KEYS = {"ok", "type", "details", "stats"}


def find_rust_binary(explicit: str | os.PathLike[str] | None = None) -> str | None:
    """Locate ``proofxiv-check``: the explicit path, $PROOFXIV_CHECK_BIN, $PATH, or the crate's build dir."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("PROOFXIV_CHECK_BIN"):
        candidates.append(Path(os.environ["PROOFXIV_CHECK_BIN"]))
    on_path = shutil.which("proofxiv-check")
    if on_path:
        candidates.append(Path(on_path))
    crate = Path(__file__).resolve().parents[3] / "checkers-rs" / "target"
    candidates += [crate / "release" / "proofxiv-check", crate / "debug" / "proofxiv-check"]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


Execute = Callable[[list[str], float | None], tuple[int | None, str, str, str | None]]
"""How to run one implementation: (command, timeout) -> (exit code, stdout, stderr, failure).
The exit code is None when the process did not finish; the runner passes a sandboxed one."""


def local_execute(cmd: list[str], timeout: float | None) -> tuple[int | None, str, str, str | None]:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, "", "", f"timed out after {timeout} s"
    except OSError as exc:
        return None, "", "", f"cannot run {cmd[0]}: {exc}"
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
        None,
    )


def _run(cmd: list[str], timeout: float | None, execute: Execute = local_execute) -> dict[str, Any]:
    start = time.monotonic()
    code, out, stderr, failure = execute(cmd, timeout)
    seconds = time.monotonic() - start
    if code is None:
        return {"exit_code": None, "result": None, "seconds": seconds, "error": failure}
    lines = [line for line in out.splitlines() if line.strip()]
    result = None
    error = None
    if len(lines) != 1:
        error = f"expected one JSON line on stdout, got {len(lines)}"
    else:
        try:
            result = json.loads(lines[0])
        except ValueError as exc:
            error = f"stdout is not JSON: {exc}"
        else:
            if not isinstance(result, dict) or set(result) != _EXPECTED_KEYS:
                error = "stdout JSON does not have exactly the keys ok, type, details, stats"
                result = None
    if result is not None and code not in (0, 1, 2):
        error = f"unexpected exit code {code}"
    if result is not None and result.get("ok") is True and code != 0:
        error = "ok is true but the exit code is not 0"
    if error and stderr:
        error += f"; stderr: {stderr[-500:]}"
    return {"exit_code": code, "result": result, "seconds": seconds, "error": error}


def run_both(
    certificate_type: str,
    path: str | os.PathLike[str],
    rust_binary: str | os.PathLike[str] | None = None,
    timeout: float | None = None,
    execute: Execute = local_execute,
) -> dict[str, Any]:
    """Run both implementations on ``path``.

    Returns ``{"implementations": {"python": bool, "rust": bool}, "agree": bool, "ok": bool,
    "details": str, "results": ..., "exit_codes": ..., "seconds": ...}``.  An implementation's
    value is True only if it exited with 0 and printed ``"ok": true``.  ``agree`` means equal exit
    codes and identical ``stats``; ``ok`` means both accepted and they agree.
    """
    path = str(path)
    runs: dict[str, dict[str, Any]] = {}
    runs["python"] = _run(
        [sys.executable, "-m", "proofxiv_worker.checkers", certificate_type, path], timeout, execute
    )
    binary = find_rust_binary(rust_binary)
    if binary is None:
        runs["rust"] = {
            "exit_code": None,
            "result": None,
            "seconds": 0.0,
            "error": "proofxiv-check binary not found (set PROOFXIV_CHECK_BIN or build worker/checkers-rs)",
        }
    else:
        runs["rust"] = _run([binary, certificate_type, path], timeout, execute)

    implementations = {}
    parts = []
    for name, run in runs.items():
        res = run["result"]
        accepted = bool(
            run["error"] is None
            and res is not None
            and res.get("ok") is True
            and run["exit_code"] == 0
        )
        implementations[name] = accepted
        if run["error"]:
            parts.append(f"{name}: ERROR {run['error']}")
        else:
            parts.append(f"{name}: exit {run['exit_code']}: {(res or {}).get('details', '')}")
    py, rs = runs["python"], runs["rust"]
    agree = (
        py["error"] is None
        and rs["error"] is None
        and py["exit_code"] == rs["exit_code"]
        and py["result"].get("stats") == rs["result"].get("stats")
    )
    if py["error"] is None and rs["error"] is None and not agree:
        if py["exit_code"] != rs["exit_code"]:
            parts.append(
                f"DISAGREEMENT: exit codes python={py['exit_code']} rust={rs['exit_code']}"
            )
        else:
            parts.append(
                f"DISAGREEMENT in stats: python={json.dumps(py['result'].get('stats'))[:400]} "
                f"rust={json.dumps(rs['result'].get('stats'))[:400]}"
            )
    return {
        "type": certificate_type,
        "implementations": implementations,
        "agree": agree,
        "ok": agree and all(implementations.values()),
        "details": " | ".join(parts),
        "results": {name: run["result"] for name, run in runs.items()},
        "exit_codes": {name: run["exit_code"] for name, run in runs.items()},
        "seconds": {name: round(run["seconds"], 3) for name, run in runs.items()},
        "rust_binary": binary,
    }
