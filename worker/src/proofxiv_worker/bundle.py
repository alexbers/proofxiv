"""Pack a submission folder (with paper.json at its root) into a .tar.gz bundle."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from proofxiv_protocol.papers import PaperManifest

SKIP_DIRS = {".git", "__pycache__", ".lake", "build", ".DS_Store", "node_modules"}
SKIP_SUFFIXES = {".aux", ".log", ".out", ".fls", ".fdb_latexmk", ".synctex.gz", ".olean", ".ilean"}


def load_manifest(folder: Path) -> PaperManifest:
    path = folder / "paper.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found (run `proofxiv init-paper {folder}`)")
    return PaperManifest.model_validate(json.loads(path.read_text()))


def collect_files(folder: Path, manifest: PaperManifest) -> list[Path]:
    """paper.json, every referenced file, plus the rest of the folder minus build junk."""
    files: list[Path] = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if any(part in SKIP_DIRS for part in rel.parts) or path.is_symlink() or not path.is_file():
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        files.append(path)
    present = {str(p.relative_to(folder)) for p in files}
    missing = sorted(manifest.referenced_files() - present)
    if missing:
        raise FileNotFoundError(f"files referenced in paper.json are missing: {missing}")
    return files


def pack(folder: Path) -> tuple[PaperManifest, bytes, int]:
    manifest = load_manifest(folder)
    files = collect_files(folder, manifest)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in files:
            tar.add(path, arcname=str(path.relative_to(folder)), recursive=False)
    return manifest, buf.getvalue(), len(files)


TEMPLATE = {
    "title": "Title of the paper",
    "abstract": "One paragraph: exactly what is proved, nothing more.",
    "byline": "Claude (AI model by Anthropic) — run by <your GitHub login>",
    "problem_slugs": [],
    "claims": [
        {
            "key": "main",
            "kind": "lean",
            "statement": "Main theorem in words (Markdown + LaTeX).",
            "lean_file": "lean/Main.lean",
            "lean_theorems": ["main_theorem"],
        }
    ],
    "novelty": {
        "what_is_new": "What exactly is new compared with the literature.",
        "prior_best": {"description": "Best previous result", "source": "https://..."},
        "sources_searched": ["arXiv", "OEIS", "the problem's survey"],
    },
    "files": {"pdf": "paper.pdf", "source": ["build/main.tex"], "extra": []},
    "author_reviews": [{"file": "REVIEW.md", "model": "model that wrote the review"}],
    "ai_contribution": "solution",
    "human_involvement": 1,
    "lean_toolchain": "leanprover/lean4:v4.35.0",
}


def write_template(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "paper.json"
    if path.exists():
        raise FileExistsError(f"{path} already exists")
    path.write_text(json.dumps(TEMPLATE, indent=2, ensure_ascii=False) + "\n")
    return path
