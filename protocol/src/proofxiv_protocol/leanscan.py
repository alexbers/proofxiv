"""Static rules for submitted Lean files, shared by the server (C1) and the runner.

A checked file may only state and prove: no code that runs while the file is being checked,
no new syntax or elaborators, no axioms, nothing that stops checking early. These are exactly
the means to forge declarations or the runner's report; honest proofs do not need them, and
computations belong in scripts. `runs_code` is the narrower rule for every Lean file in a bundle.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"')

# token -> why it is refused
FORBIDDEN = {
    "axiom": "declares an `axiom`",
    "#exit": "stops checking early (`#exit`)",
    "#eval": "runs code while being checked (`#eval`); move computations to a script",
    "#eval!": "runs code while being checked (`#eval!`)",
    "run_cmd": "runs code while being checked (`run_cmd`)",
    "run_elab": "runs code while being checked (`run_elab`)",
    "run_meta": "runs code while being checked (`run_meta`)",
    "run_tac": "runs code while being checked (`run_tac`)",
    "by_elab": "runs code while being checked (`by_elab`)",
    "macro": "defines a macro",
    "macro_rules": "defines macro rules",
    "syntax": "defines new syntax",
    "declare_syntax_cat": "defines new syntax",
    "elab": "defines an elaborator",
    "elab_rules": "defines elaborator rules",
    "initialize": "runs code at import time (`initialize`)",
    "builtin_initialize": "runs code at import time (`builtin_initialize`)",
    "unsafe": "uses `unsafe` code",
    "implemented_by": "replaces code the kernel checked (`implemented_by`)",
    "extern": "uses external code (`extern`)",
    "include_str": "reads files while being checked (`include_str`)",
    "debug.skipKernelTC": "turns off kernel checking",
}
# every token the rules look at, in one pass (longest first, so #eval! wins over #eval)
_TOKENS = sorted({"sorry", "admit", "native_decide", *FORBIDDEN}, key=len, reverse=True)
_ANY_TOKEN = re.compile(
    r"(?<![\w.'«])(" + "|".join(re.escape(t) for t in _TOKENS) + r")(?![\w'»!])"
)

# Axioms that `native_decide` introduces: old toolchains use Lean.ofReduceBool; Lean 4.2x and
# later add one auxiliary axiom per use, named `<decl>._native.native_decide.ax_<i>_<j>`.
_NATIVE_AXIOM = re.compile(r"\._native\.native_decide\.ax(?:_\d+)+$")
NATIVE_DECIDE_AXIOMS = frozenset({"Lean.ofReduceBool", "Lean.trustCompiler"})

_IMPORT = re.compile(
    r"^[ \t]*(?:public[ \t]+)?(?:meta[ \t]+)?import[ \t]+(?:all[ \t]+)?([\w.]+(?:[ \t]+[\w.]+)*)",
    re.MULTILINE,
)
_VERSION = re.compile(r"(\d+\.\d+\.\d+)")
MATHLIB_ROOTS = frozenset({"Mathlib", "Batteries", "Aesop", "Qq", "ProofWidgets", "Plausible"})
LIBRARY_ROOTS = MATHLIB_ROOTS | {"Init", "Std", "Lean", "LeanSearchClient", "ImportGraph"}


def strip_comments(source: str) -> str:
    """Remove `--` line comments and nested `/- ... -/` block comments (keeps line breaks)."""
    code, _ = _strip(source)
    return code


_COMMENT_TOKENS = re.compile(r'"|--|/-')
_STRING_BODY = re.compile(r'(?:\\.|[^"\\]|\\\Z)*(?:"|\Z)', re.DOTALL)


def _strip(source: str) -> tuple[str, bool]:
    """(code without comments, whether a block comment is left open at the end).

    Jumps between the few characters that matter (quote, --, /-, -/), so long data files are
    cheap. Strings are kept verbatim; comments nest; line breaks inside comments are kept."""
    out: list[str] = []
    pos, depth, n = 0, 0, len(source)
    while pos < n:
        if depth == 0:
            match = _COMMENT_TOKENS.search(source, pos)
            if match is None:
                out.append(source[pos:])
                break
            token, at = match.group(), match.start()
            if token == '"':
                body = _STRING_BODY.match(source, at + 1)
                assert body is not None
                out.append(source[pos : body.end()])
                pos = body.end()
            elif token == "--":
                out.append(source[pos:at])
                newline = source.find("\n", at)
                pos = n if newline < 0 else newline
            else:  # /-
                out.append(source[pos:at])
                depth, pos = 1, at + 2
        else:
            match = _BLOCK_TOKENS.search(source, pos)
            if match is None:
                out.append("\n" * source.count("\n", pos))
                pos = n
                break
            out.append("\n" * source.count("\n", pos, match.start()))
            depth += 1 if match.group() == "/-" else -1
            pos = match.end()
    return "".join(out), depth > 0


_BLOCK_TOKENS = re.compile(r"/-|-/")


def scan_lean(source: str) -> tuple[list[str], set[str]]:
    """Return (blocking issues, informational flags) for a Lean source file."""
    code, open_comment = _strip(source)
    found = {m.group(1) for m in _ANY_TOKEN.finditer(_STRING.sub('""', code))}
    issues: list[str] = []
    if open_comment:
        issues.append("Lean file ends inside a block comment")
    if "sorry" in found:
        issues.append("Lean file contains `sorry`")
    if "admit" in found:
        issues.append("Lean file contains `admit`")
    issues += [f"Lean file {why}" for token, why in FORBIDDEN.items() if token in found]
    return issues, ({"native_decide"} if "native_decide" in found else set())


# Ways a Lean file runs code on the machine of whoever compiles it (or opens it in an editor):
# commands that evaluate, elaborators and initializers, extensions registered by attribute, and the
# IO API such code would call. Refused in every Lean file of a bundle and in problem statements, so
# that a reviewer who compiles a paper does not run its author's program.
RUNS_CODE = {
    "#eval": "`#eval` (for a pure check use `#guard` or `example … := by decide`, else a script)",
    "#eval!": "`#eval!`",
    "run_cmd": "`run_cmd`",
    "run_elab": "`run_elab`",
    "run_meta": "`run_meta`",
    "run_tac": "`run_tac`",
    "by_elab": "`by_elab`",
    "elab": "an elaborator (`elab`)",
    "elab_rules": "elaborator rules (`elab_rules`)",
    "simproc": "a simp procedure (`simproc`)",
    "dsimproc": "a simp procedure (`dsimproc`)",
    "simproc_decl": "a simp procedure (`simproc_decl`)",
    "dsimproc_decl": "a simp procedure (`dsimproc_decl`)",
    "initialize": "code at import time (`initialize`)",
    "builtin_initialize": "code at import time (`builtin_initialize`)",
    "unsafe": "`unsafe` code",
    "extern": "external code (`extern`)",
    "include_str": "reading files at compile time (`include_str`)",
}
_RUNS_CODE_TOKEN = re.compile(
    r"(?<![\w.'«])(" + "|".join(re.escape(t) for t in sorted(RUNS_CODE, key=len, reverse=True)) + r")(?![\w'»!])"
)
# attributes that register code the elaborator, a tactic or the editor will run
_CODE_ATTRIBUTE = re.compile(
    r"(?:@\[|\battribute\s*\[)[^\]]*?(?<![\w.])(init|builtin_init|command_elab|term_elab|tactic|"
    r"builtin_command_elab|builtin_term_elab|builtin_tactic|command_code_action|hole_code_action|"
    r"tactic_code_action|widget_module|delab|simproc|norm_num|positivity|env_linter)(?![\w.])"
)
_IO_API = re.compile(
    r"(?<![\w.])(?:IO\.)?(?:Process\.(?:run|spawn|output|exit)|FS\.(?:readFile|writeFile|readBinFile|"
    r"writeBinFile|Handle|createDirAll|removeFile|removeDirAll|lines))(?![\w'])|"
    r"(?<![\w.])(?:IO\.(?:Process|FS|getEnv)|loadDynlib)(?![\w'])"
)


def runs_code(source: str) -> list[str]:
    """How this Lean file would run code when compiled, one reason per mechanism (empty if none)."""
    # «IO».Process is IO.Process: drop the name quotes before matching
    code = _STRING.sub('""', _strip(source)[0]).replace("«", "").replace("»", "")
    found = {m.group(1) for m in _RUNS_CODE_TOKEN.finditer(code)}
    reasons = [RUNS_CODE[token] for token in RUNS_CODE if token in found]
    reasons += sorted({f"the `{m.group(1)}` attribute" for m in _CODE_ATTRIBUTE.finditer(code)})
    if _IO_API.search(code):
        reasons.append("the IO API (processes, files or the environment)")
    return reasons


def statement_problems(source: str) -> list[str]:
    """Why a problem's Lean statement is refused: it is a fragment over Mathlib (no imports) that
    states a theorem with `sorry`, and must not run code, add axioms or stop checking."""
    code = _STRING.sub('""', _strip(source)[0])
    problems = [f"runs code when compiled: {reason}" for reason in runs_code(source)]
    if _IMPORT.search(code):
        problems.append("has `import` lines: a statement is a fragment over Mathlib")
    found = {m.group(1) for m in _ANY_TOKEN.finditer(code)}
    for token in ("axiom", "#exit", "debug.skipKernelTC", "macro", "macro_rules", "syntax", "declare_syntax_cat", "implemented_by"):
        if token in found:
            problems.append(FORBIDDEN[token])
    return problems


def is_native_decide_axiom(name: str) -> bool:
    return name in NATIVE_DECIDE_AXIOMS or bool(_NATIVE_AXIOM.search(name))


def imported_modules(source: str) -> list[str]:
    code = strip_comments(source)
    return [m for match in _IMPORT.finditer(code) for m in match.group(1).split()]


def local_imports(source: str) -> list[str]:
    """Imported modules that are not from Lean, Mathlib or their dependencies."""
    return [m for m in imported_modules(source) if m.split(".")[0] not in LIBRARY_ROOTS]


def uses_mathlib(source: str) -> bool:
    return any(m.split(".")[0] in MATHLIB_ROOTS for m in imported_modules(source))


def module_path(root: str, module: str) -> str:
    """Bundle path of a local module: `A.B` lives at `<root>/A/B.lean`."""
    rel = module.replace(".", "/") + ".lean"
    return f"{root}/{rel}" if root else rel


def local_modules(
    sources: dict[str, str], main: str, imports_of: Callable[[str], list[str]] | None = None
) -> tuple[list[tuple[str, str]], list[str]]:
    """Local modules a Lean file needs, resolved under the file's own directory, in an order
    where each module comes after the modules it imports: [(module name, bundle path)].
    Also returns problems (a module missing from the bundle, an import cycle)."""

    def from_sources(path: str) -> list[str]:
        return local_imports(sources[path])

    imports = imports_of or from_sources
    root = main.rsplit("/", 1)[0] if "/" in main else ""
    order: list[tuple[str, str]] = []
    problems: list[str] = []
    state: dict[str, str] = {}  # module -> "visiting" | "done"

    def visit(module: str, chain: list[str]) -> None:
        if state.get(module) == "done":
            return
        if state.get(module) == "visiting":
            problems.append("import cycle: " + " -> ".join([*chain, module]))
            return
        path = module_path(root, module)
        if path not in sources:
            problems.append(f"imports `{module}`, but {path} is not in the bundle")
            return
        state[module] = "visiting"
        for dep in imports(path):
            visit(dep, [*chain, module])
        state[module] = "done"
        order.append((module, path))

    for dep in imports(main):
        visit(dep, [])
    return order, problems


def lean_version(toolchain: str | None) -> str | None:
    """'leanprover/lean4:v4.35.0' -> '4.35.0'."""
    match = _VERSION.search(toolchain or "")
    return match.group(1) if match else None


_DECLARED = re.compile(r"\b(?:theorem|lemma|def|abbrev|instance)\s+([^\s:({\[]+)")


def _declaration(name: str) -> re.Pattern[str]:
    short = name.rsplit(".", 1)[-1]
    return re.compile(rf"\b(?:theorem|lemma|def|abbrev|instance)\s+(?:\S+\.)?{re.escape(short)}\b")


def declares_theorem(source: str, name: str) -> bool:
    return bool(_declaration(name).search(strip_comments(source)))


class LeanFiles:
    """The Lean sources of one bundle with per-file results cached: a bundle may carry
    hundreds of claims on the same large file."""

    def __init__(self, sources: dict[str, str]) -> None:
        self.sources = sources
        self._code: dict[str, str] = {}
        self._scan: dict[str, tuple[list[str], set[str]]] = {}
        self._imports: dict[str, list[str]] = {}
        self._modules: dict[str, tuple[list[tuple[str, str]], list[str]]] = {}
        self._declared: dict[str, set[str]] = {}

    def code(self, path: str) -> str:
        if path not in self._code:
            self._code[path] = strip_comments(self.sources[path])
        return self._code[path]

    def scan(self, path: str) -> tuple[list[str], set[str]]:
        if path not in self._scan:
            self._scan[path] = scan_lean(self.sources[path])
        return self._scan[path]

    def imports(self, path: str) -> list[str]:
        if path not in self._imports:
            self._imports[path] = [m for match in _IMPORT.finditer(self.code(path)) for m in match.group(1).split()]
        return self._imports[path]

    def local_imports(self, path: str) -> list[str]:
        return [m for m in self.imports(path) if m.split(".")[0] not in LIBRARY_ROOTS]

    def modules(self, main: str) -> tuple[list[tuple[str, str]], list[str]]:
        if main not in self._modules:
            self._modules[main] = local_modules(self.sources, main, self.local_imports)
        return self._modules[main]

    def uses_mathlib(self, path: str) -> bool:
        return any(m.split(".")[0] in MATHLIB_ROOTS for m in self.imports(path))

    def declared(self, path: str) -> set[str]:
        """Last name components of everything the file declares (one pass per file)."""
        if path not in self._declared:
            found = _DECLARED.finditer(self.code(path))
            self._declared[path] = {m.group(1).rsplit(".", 1)[-1] for m in found}
        return self._declared[path]

    def declares(self, paths: list[str], name: str) -> bool:
        short = name.rsplit(".", 1)[-1]
        if any(short in self.declared(p) for p in paths):
            return True
        pattern = _declaration(name)  # the plain rule, for anything the index does not cover
        return any(pattern.search(self.code(p)) for p in paths)
