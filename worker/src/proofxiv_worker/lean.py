"""Checking a Lean file: compile it in the sandbox and record the axioms of each theorem."""

from __future__ import annotations

import functools
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from proofxiv_protocol.leanscan import local_imports, local_modules, scan_lean, uses_mathlib
from proofxiv_protocol.runs import LeanReport

from .config import Config

__all__ = ["local_imports", "local_modules", "scan_lean", "uses_mathlib"]  # re-exported for the runner

VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
AXIOMS_RE = re.compile(r"'(?P<name>[^']+)' depends on axioms: \[(?P<axioms>[^\]]*)\]")
NO_AXIOMS_RE = re.compile(r"'(?P<name>[^']+)' does not depend on any axioms")
SORRY_RE = re.compile(r"declaration uses [`']sorry[`']")  # Lean 4.3x quotes it with backticks


class LeanSetupError(RuntimeError):
    pass


@dataclass
class LeanEnv:
    lean: Path
    ro_paths: list[Path]
    env: dict[str, str]
    description: str


def _toolchain_version(text: str) -> str | None:
    match = VERSION_RE.search(text)
    return match.group(1) if match else None


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise LeanSetupError(f"git {' '.join(args)} failed in {repo}: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


@functools.lru_cache(maxsize=8)
def verify_project(project: Path, mathlib_commit: str | None) -> None:
    """The Mathlib project the runner checks against is exactly what its manifest pins: every
    package at its commit with no local changes, and Mathlib at the commit the operator chose."""
    manifest = json.loads((project / "lake-manifest.json").read_text())
    for package in manifest.get("packages", []):
        name, rev = package["name"], package.get("rev", "")
        repo = project / ".lake" / "packages" / name
        head = _git(repo, "rev-parse", "HEAD")
        if head != rev:
            raise LeanSetupError(f"package {name} is at {head[:12]}, the manifest pins {rev[:12]}")
        if _git(repo, "status", "--porcelain", "--untracked-files=no"):
            raise LeanSetupError(f"package {name} has local changes")
    mathlib = next((p for p in manifest.get("packages", []) if p["name"] == "mathlib"), None)
    if mathlib_commit and (mathlib is None or mathlib.get("rev") != mathlib_commit):
        found = mathlib.get("rev", "")[:12] if mathlib else "none"
        raise LeanSetupError(f"the project's Mathlib is {found}, the runner is configured for {mathlib_commit[:12]}")


def resolve_env(config: Config, image: str, needs_mathlib: bool) -> LeanEnv:
    version = _toolchain_version(image)
    if needs_mathlib:
        projects = config.lean_projects
        project_dir = projects.get(version or "") or (next(iter(projects.values())) if len(projects) == 1 else None)
        if project_dir is None:
            raise LeanSetupError(f"no Mathlib project configured for Lean {version}")
        project = Path(project_dir)
        verify_project(project, config.mathlib_commits.get(version or ""))
        project_version = _toolchain_version((project / "lean-toolchain").read_text())
        toolchain_dir = config.lean_toolchains.get(project_version or "")
        if toolchain_dir is None:
            raise LeanSetupError(f"no toolchain configured for the project's Lean {project_version}")
        toolchain = Path(toolchain_dir)
        lean_path = [toolchain / "lib" / "lean", project / ".lake" / "build" / "lib" / "lean"]
        lean_path += sorted((project / ".lake" / "packages").glob("*/.lake/build/lib/lean"))
        return LeanEnv(
            lean=toolchain / "bin" / "lean",
            ro_paths=[toolchain, project],
            env={"LEAN_PATH": ":".join(str(p) for p in lean_path)},
            description=f"Lean {project_version} + Mathlib project {project.name}",
        )
    toolchains = config.lean_toolchains
    toolchain_dir = toolchains.get(version or "") or (next(iter(toolchains.values())) if len(toolchains) == 1 else None)
    if toolchain_dir is None:
        raise LeanSetupError(f"no Lean toolchain configured for version {version}")
    toolchain = Path(toolchain_dir)
    return LeanEnv(
        lean=toolchain / "bin" / "lean",
        ro_paths=[toolchain],
        env={},
        description=f"Lean core toolchain {toolchain.name}",
    )


def check_source(source: str, theorems: list[str], nonce: str) -> str:
    """The submitted file plus the runner's own commands, fenced by markers with a fresh nonce:
    only output between the markers counts, and a file that keeps them from running fails."""
    lines = [source.rstrip(), "", "-- appended by the ProofXiv runner", f'#print "PROOFXIV-BEGIN-{nonce}"']
    lines += [f"#print axioms {name}" for name in theorems]
    lines.append(f'#print "PROOFXIV-END-{nonce}"')
    return "\n".join(lines) + "\n"


def parse_output(text: str, nonce: str) -> LeanReport:
    errors = [line.strip() for line in text.splitlines() if ": error" in line][:50]
    begin, end = f"PROOFXIV-BEGIN-{nonce}", f"PROOFXIV-END-{nonce}"
    start, stop = text.find(begin), text.rfind(end)
    if start < 0 or stop < start:
        errors.append("the runner's own checks did not run (the file stopped Lean early)")
        return LeanReport(errors=errors, has_sorry=bool(SORRY_RE.search(text)))
    ours = text[start + len(begin) : stop]
    theorems: dict[str, list[str]] = {}
    for match in AXIOMS_RE.finditer(ours):
        theorems[match.group("name")] = [
            a.strip() for a in match.group("axioms").replace("\n", " ").split(",") if a.strip()
        ]
    for match in NO_AXIOMS_RE.finditer(ours):
        theorems[match.group("name")] = []
    return LeanReport(
        theorems=theorems,
        has_sorry=bool(SORRY_RE.search(text)),
        errors=errors,
    )
