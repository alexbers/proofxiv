"""Static rules for submitted Lean files (shared by the server and the runner)."""

from __future__ import annotations

import pytest
from proofxiv_protocol.leanscan import (
    imported_modules,
    is_native_decide_axiom,
    local_imports,
    scan_lean,
)
from proofxiv_protocol.runs import LeanReport

HONEST = """import Mathlib
/- A comment may mention axiom, #eval, macro and #exit freely. -/
-- so may this one: run_cmd, implemented_by
notation "‖" x "‖" => abs x

theorem big : (List.range 200).foldl (· + ·) 0 = 19900 := by native_decide
theorem s : "axiom #exit".length = 11 := by decide
#print axioms big
"""


def test_honest_file_passes_with_native_decide_flag():
    issues, flags = scan_lean(HONEST)
    assert issues == [] and flags == {"native_decide"}


@pytest.mark.parametrize(
    "line, why",
    [
        ("theorem t : True := trivial axiom evil : False", "declares an `axiom`"),
        ("#exit", "#exit"),
        ("#eval IO.println \"'t' depends on axioms: [propext]\"", "#eval"),
        ("#eval! 1", "#eval!"),
        ("macro \"mk\" : command => `(theorem x : True := trivial)", "macro"),
        ("syntax \"foo\" : term", "new syntax"),
        ("elab \"bar\" : tactic => pure ()", "elaborator"),
        ("run_cmd pure ()", "run_cmd"),
        ("example : True := by run_tac pure ()", "run_tac"),
        ("@[implemented_by id] def f (n : Nat) : Nat := n", "implemented_by"),
        ("@[extern \"c_f\"] opaque g : Nat → Nat", "extern"),
        ("unsafe def h : Nat := 0", "unsafe"),
        ("initialize pure ()", "import time"),
        ("def s : String := include_str \"Main.lean\"", "include_str"),
        ("set_option debug.skipKernelTC true", "kernel checking"),
        ("/- an unterminated comment hides the runner's commands", "block comment"),
    ],
)
def test_forbidden_constructs(line, why):
    issues, _ = scan_lean(f"theorem ok : True := trivial\n{line}\n")
    assert any(why in issue for issue in issues), issues


def test_native_decide_axioms():
    assert is_native_decide_axiom("big_check._native.native_decide.ax_1_1")
    assert is_native_decide_axiom("Ns.rows_chkP._native.native_decide.ax_12_3")
    assert is_native_decide_axiom("Lean.ofReduceBool")
    assert not is_native_decide_axiom("evil")
    assert not is_native_decide_axiom("x._native.native_decide.lemma")
    report = LeanReport(
        theorems={"main": ["propext", "Quot.sound", "main._native.native_decide.ax_1_1", "sorryAx"]}
    )
    assert report.uses_native_decide() and report.nonstandard_axioms() == {"sorryAx"}


def test_imports_stop_at_the_line_end():
    source = "import Mathlib.Tactic\nimport Foo.Bar Baz\ntheorem main : True := trivial\n"
    assert imported_modules(source) == ["Mathlib.Tactic", "Foo.Bar", "Baz"]
    assert local_imports("import Mathlib\n\ntheorem t : True := trivial\n") == []
