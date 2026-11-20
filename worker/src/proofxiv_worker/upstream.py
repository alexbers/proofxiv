"""Pull requests to formal-conjectures for statements settled on ProofXiv, opened by a person.

`proofxiv owner upstream open <slug>` makes the edit formal-conjectures' CONTRIBUTING asks for
(`category research open` -> `research solved`, plus `formal_proof using lean4 at "<Palomar>"` when a
registered Lean proof exists) in a fresh checkout of upstream main, shows the diff and the
pull-request text, and pushes and opens the PR with the GitHub CLI only after the person types
"yes". The PR comes from that person's GitHub account; nothing is opened from the server.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

UPSTREAM = "google-deepmind/formal-conjectures"


class UpstreamError(RuntimeError):
    pass


def mark_solved(text: str, name: str, formal_proof: str | None) -> str:
    """The file with the attribute of `theorem name` changed from `research open` to `research
    solved` (and a `formal_proof` link added). Raises if the statement or its tag is not found."""
    decl = re.search(r"(?:theorem|lemma)\s+" + re.escape(name) + r"(?=[\s(:{\[])", text)
    if decl is None:
        raise UpstreamError(f"`{name}` is not in the file any more")
    start = text.rfind("@[", 0, decl.start())
    end = text.find("]", start)
    if start < 0 or end < 0 or end > decl.start() or text[end + 1 : decl.start()].strip():
        raise UpstreamError(f"no attribute right before `{name}`")
    attrs = text[start + 2 : end]
    if not re.search(r"category\s+research\s+open", attrs):
        raise UpstreamError(f"`{name}` is not tagged `research open` any more")
    attrs = re.sub(r"category\s+research\s+open", "category research solved", attrs, count=1)
    if formal_proof and "formal_proof" not in attrs:
        attrs = f'{attrs.rstrip()},\n  formal_proof using lean4 at "{formal_proof}"'
    return text[: start + 2] + attrs + text[end:]


def _run(args: list[str], cwd: Path | None = None) -> str:
    done = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if done.returncode != 0:
        raise UpstreamError(f"{' '.join(args[:3])} failed: {(done.stderr or done.stdout).strip()[:500]}")
    return done.stdout.strip()


def prepare(candidate: dict[str, Any], workdir: Path) -> tuple[Path, str, str]:
    """A checkout of upstream main with the edit on branch proofxiv/<slug> in the person's fork.
    Returns (checkout, branch, diff)."""
    if shutil.which("gh") is None:
        raise UpstreamError("the GitHub CLI (gh) is needed: install it and run `gh auth login`")
    login = _run(["gh", "api", "user", "--jq", ".login"])
    _run(["gh", "repo", "fork", UPSTREAM, "--clone=false"])  # no-op when the fork exists
    checkout = workdir / "formal-conjectures"
    if not (checkout / ".git").exists():
        _run(["git", "clone", "--depth", "1", f"https://github.com/{login}/formal-conjectures.git", str(checkout)])
        _run(["git", "remote", "add", "upstream", f"https://github.com/{UPSTREAM}.git"], cwd=checkout)
    _run(["git", "fetch", "--depth", "1", "upstream", "main"], cwd=checkout)
    branch = f"proofxiv/{candidate['slug']}"[:100]
    _run(["git", "checkout", "-B", branch, "upstream/main"], cwd=checkout)
    path = checkout / candidate["path"]
    if not path.is_file():
        raise UpstreamError(f"{candidate['path']} is not in upstream main any more")
    path.write_text(mark_solved(path.read_text(), candidate["name"], candidate["edit"].get("formal_proof")))
    return checkout, branch, _run(["git", "diff"], cwd=checkout)


def open_pull_request(
    candidate: dict[str, Any], workdir: Path, confirm: Callable[[str], bool], dry_run: bool = False
) -> str | None:
    """Prepare, show, and on confirmation push and open the PR; the PR's URL, or None."""
    checkout, branch, diff = prepare(candidate, workdir)
    preview = f"{diff}\n\nTitle: {candidate['pr_title']}\n\n{candidate['pr_body']}\n"
    if candidate.get("pull_requests_seen"):
        preview += "\nPull requests already seen for this statement:\n" + "\n".join(
            f"  {pr['title']} {pr['url']}" for pr in candidate["pull_requests_seen"]
        )
    if dry_run or not confirm(preview):
        _run(["git", "checkout", "--", "."], cwd=checkout)
        return None
    login = _run(["gh", "api", "user", "--jq", ".login"])
    _run(["git", "commit", "-am", candidate["pr_title"]], cwd=checkout)
    _run(["git", "push", "--force", "-u", "origin", branch], cwd=checkout)
    return _run(
        ["gh", "pr", "create", "--repo", UPSTREAM, "--base", "main", "--head", f"{login}:{branch}",
         "--title", candidate["pr_title"], "--body", candidate["pr_body"]],
        cwd=checkout,
    )
