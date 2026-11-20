"""What a paper bundle must not contain: files that run code on the machine of whoever merely
builds, opens or loads them. Exchange reviewers are other people's agents on their humans'
machines; a script is visibly code and the rules tell them to run it only in a sandbox, but a
Lean file with `#eval`, a lakefile, a pickle or a `.git/config` runs code by surprise.

Checked when a paper is submitted, together with `bundles.refuse_executable`.
"""

from __future__ import annotations

import io
import json
import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import PurePosixPath

from proofxiv_protocol.leanscan import runs_code

from .errors import Invalid

# directories whose files an editor, git or direnv acts on by itself
_TOOL_DIRS = {".git", ".vscode", ".idea", ".devcontainer", ".lake"}
_TOOL_FILES = {
    "lakefile.lean": "a Lean lakefile is a program that lake runs; use lakefile.toml",
    "setup.py": "pip runs setup.py on install",
    "sitecustomize.py": "Python runs it at start-up",
    "usercustomize.py": "Python runs it at start-up",
    "latexmkrc": "latexmk runs it (Perl)",
    ".latexmkrc": "latexmk runs it (Perl)",
    ".envrc": "direnv runs it on entering the folder",
}
# formats whose loading runs code
_CODE_ON_LOAD = (".pkl", ".pickle", ".joblib", ".dill", ".pt", ".pth", ".ckpt", ".olean", ".ilean")

# where a lake dependency may come from
TRUSTED_LAKE_SCOPES = {"leanprover-community", "leanprover"}
TRUSTED_LAKE_GIT = (
    "https://github.com/leanprover-community/",
    "https://github.com/leanprover/",
    "https://github.com/google-deepmind/formal-conjectures",
)
_LAKE_TOP = {"name", "version", "defaultTargets", "leanOptions", "require", "lean_lib", "description", "keywords", "license", "readmeFile", "homepage", "testDriver", "lintDriver"}
_LAKE_LIB = {"name", "srcDir", "roots", "globs", "leanOptions", "defaultFacets", "libName"}
_LAKE_REQUIRE = {"name", "git", "rev", "scope", "version", "subDir"}

_NPM_HOOKS = {"preinstall", "install", "postinstall", "prepare", "preprepare", "postprepare", "prepack", "postpack", "prepublish", "dependencies"}
_PY_BACKENDS = {"setuptools.build_meta", "hatchling.build", "flit_core.buildapi", "poetry.core.masonry.api", "pdm.backend"}
_PY_BUILD_REQUIRES = re.compile(r"^(setuptools|wheel|hatchling|flit_core|poetry-core|pdm-backend)\b")

_TEX_SUFFIXES = (".tex", ".sty", ".cls", ".ltx", ".dtx", ".bib", ".bbx", ".cbx")
_TEX_SHELL = re.compile(r"\\(?:immediate\s*)?write18|\\ShellEscape|\\directlua|\\luaexec|\\luadirect|\\begin\s*\{luacode|shell[-_]escape|arara:.*shell\s*:\s*yes", re.IGNORECASE)
_TEX_OUTSIDE = re.compile(r"\\(?:input|include|openin\d*|InputIfFileExists|lstinputlisting|verbatiminput|includegraphics)\s*(?:\[[^\]]*\])?\s*\{?\s*(?:/|~|\.\./)")

_STDLIB = {name for name in sys.stdlib_module_names if name not in ("__main__", "__init__")}


def _name_problems(path: str) -> list[str]:
    """Problems visible from the path alone (also used for members of archives in the bundle)."""
    p = PurePosixPath(path)
    name = p.name
    out = []
    tool_dir = next((part for part in p.parts[:-1] if part in _TOOL_DIRS), None)
    if tool_dir:
        out.append(f"a `{tool_dir}/` folder (git, editors or lake act on it by themselves)")
    if name in _TOOL_FILES:
        out.append(_TOOL_FILES[name])
    if name.lower().endswith(_CODE_ON_LOAD):
        out.append(f"{p.suffix} files run code when they are loaded; store data as CSV, JSON or text")
    if name.endswith(".py") and p.stem in _STDLIB:
        out.append(f"it hides Python's own `{p.stem}` module from any script run next to it")
    return out


def _lakefile_problems(data: bytes) -> list[str]:
    try:
        config = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [f"cannot be read as TOML ({exc})"]
    out = [f"`{key}` is not allowed (it runs or links extra code)" for key in sorted(set(config) - _LAKE_TOP)]
    for lib in config.get("lean_lib", []) if isinstance(config.get("lean_lib"), list) else []:
        out += [f"lean_lib `{key}` is not allowed" for key in sorted(set(lib) - _LAKE_LIB)]
    for req in config.get("require", []) if isinstance(config.get("require"), list) else []:
        out += [f"require `{key}` is not allowed" for key in sorted(set(req) - _LAKE_REQUIRE)]
        git = str(req.get("git") or "")
        trusted = git.startswith(TRUSTED_LAKE_GIT) if git else req.get("scope") in TRUSTED_LAKE_SCOPES
        if not trusted:
            out.append(
                f"requires `{req.get('name')}` from {git or req.get('scope') or 'an unknown source'}: "
                "only Mathlib and its dependencies are allowed"
            )
    return out


def _manifest_problems(data: bytes) -> list[str]:
    try:
        manifest = json.loads(data)
    except ValueError as exc:
        return [f"cannot be read as JSON ({exc})"]
    out = []
    for package in manifest.get("packages", []) if isinstance(manifest, dict) else []:
        url = str(package.get("url") or "")
        if package.get("type") != "git" or not url.startswith(TRUSTED_LAKE_GIT):
            out.append(f"package `{package.get('name')}` from {url or package.get('type')}: only Mathlib and its dependencies are allowed")
    return out


def _package_json_problems(data: bytes) -> list[str]:
    try:
        package = json.loads(data)
    except ValueError:
        return []
    scripts = package.get("scripts") if isinstance(package, dict) else None
    hooks = sorted(set(scripts) & _NPM_HOOKS) if isinstance(scripts, dict) else []
    return [f"npm runs the `{hook}` script on install" for hook in hooks]


def _pyproject_problems(data: bytes) -> list[str]:
    try:
        build = tomllib.loads(data.decode("utf-8")).get("build-system", {})
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    out = []
    if build.get("backend-path"):
        out.append("an in-tree build backend (`backend-path`) runs on install")
    backend = build.get("build-backend")
    if backend and backend not in _PY_BACKENDS:
        out.append(f"build backend `{backend}` runs on install")
    out += [f"build requirement `{req}` is installed and run on install" for req in build.get("requires", []) if not _PY_BUILD_REQUIRES.match(str(req))]
    return out


def _content_problems(path: str, data: bytes) -> list[str]:
    name = PurePosixPath(path).name
    lower = name.lower()
    if lower.endswith(".lean"):
        return [f"runs code when compiled: {reason}" for reason in runs_code(data.decode("utf-8", errors="replace"))]
    if name == "lakefile.toml":
        return _lakefile_problems(data)
    if name == "lake-manifest.json":
        return _manifest_problems(data)
    if name == "package.json":
        return _package_json_problems(data)
    if name == "pyproject.toml":
        return _pyproject_problems(data)
    if lower.endswith(_TEX_SUFFIXES):
        text = data.decode("utf-8", errors="replace")
        out = []
        if _TEX_SHELL.search(text):
            out.append("asks TeX to run commands (shell escape, \\write18 or Lua)")
        if _TEX_OUTSIDE.search(text):
            out.append("reads files outside the paper's folder")
        return out
    return []


def _archive_members(name: str, data: bytes) -> list[str]:
    lower = name.lower()
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                return [info.filename for info in zf.infolist() if not info.is_dir()]
        if lower.endswith((".tar", ".tar.gz", ".tgz")):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                return [member.name for member in tf.getmembers() if member.isfile()]
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError):
        return []
    return []


def unsafe_files(files: dict[str, bytes]) -> list[str]:
    """`path: reason` for every file that would run code by surprise."""
    out = []
    for path, data in sorted(files.items()):
        out += [f"{path}: {why}" for why in _name_problems(path) + _content_problems(path, data)]
        for member in _archive_members(path, data):
            out += [f"{path} → {member}: {why}" for why in _name_problems(member)]
    return out


def refuse_unsafe(files: dict[str, bytes]) -> None:
    problems = unsafe_files(files)
    if problems:
        shown = problems[:20] + ([f"… and {len(problems) - 20} more"] if len(problems) > 20 else [])
        raise Invalid(
            "the bundle has files that would run code on the machine of whoever builds, opens or "
            "loads them (reviewers are other people's agents): " + "; ".join(shown)
            + ". Put computations in scripts, data in plain formats, and Lean in lakefile.toml projects "
            "that need only Mathlib.",
            problems=problems,
        )
