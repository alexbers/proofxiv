"""The runner reads Lean's output only between its own markers (no Lean needed here; the
real-Lean case is in test_spoofing_is_refused)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from proofxiv_worker.lean import check_source, parse_output

ROOT = Path(__file__).resolve().parents[2]
TOOLCHAIN = ROOT.parent / "attempts" / "toolchain" / "lean-4.34.1-linux"


def test_only_output_between_markers_counts():
    text = (
        "Main.lean:1:8: warning: declaration uses `sorry`\n"
        "'main' depends on axioms: [propext]\n"  # printed by the file itself
        "PROOFXIV-BEGIN-n1\n'main' depends on axioms: [sorryAx]\nPROOFXIV-END-n1\n"
    )
    report = parse_output(text, "n1")
    assert report.theorems == {"main": ["sorryAx"]} and report.has_sorry


def test_missing_markers_fail():
    report = parse_output("'main' depends on axioms: [propext]\n", "n2")
    assert report.theorems == {} and any("did not run" in e for e in report.errors)


def test_check_source_appends_fenced_commands():
    out = check_source("theorem t : True := trivial", ["t"], "abc")
    assert out.endswith('#print "PROOFXIV-BEGIN-abc"\n#print axioms t\n#print "PROOFXIV-END-abc"\n')


@pytest.mark.skipif(
    not (TOOLCHAIN / "bin" / "lean").exists() or shutil.which("bwrap") is None,
    reason="needs the local Lean toolchain and bubblewrap",
)
@pytest.mark.parametrize(
    "source, verdict",
    [
        ("theorem t : 2 + 2 = 4 := by decide\n", "pass"),
        ("theorem t : (List.range 50).length = 50 := by native_decide\n", "pass"),
        ("theorem t : 1 = 2 := by sorry\n", "fail"),
        # prints a fake report, then stops Lean before the runner's commands
        ("theorem t : 1 = 2 := by sorry\n#print \"'t' depends on axioms: [propext]\"\n#exit\n", "fail"),
    ],
)
def test_spoofing_is_refused(tmp_path, source, verdict):
    got, report, log = _run(tmp_path, {"Main.lean": source}, "Main.lean")
    assert got == verdict, log
    if verdict == "pass":
        assert report is not None and report.nonstandard_axioms() == set()


needs_lean = pytest.mark.skipif(
    not (TOOLCHAIN / "bin" / "lean").exists() or shutil.which("bwrap") is None,
    reason="needs the local Lean toolchain and bubblewrap",
)
DEFS = "def table : List Nat := List.range 30\n\ntheorem table_sum : table.sum = 435 := by native_decide\n"
MAIN = "import Proj.Defs\n\ntheorem t : table.sum = 435 := table_sum\n"


@needs_lean
def test_multi_file_project(tmp_path):
    got, report, log = _run(
        tmp_path, {"lean/Main.lean": MAIN, "lean/Proj/Defs.lean": DEFS}, "lean/Main.lean"
    )
    assert got == "pass", log
    assert report is not None and report.uses_native_decide() and report.nonstandard_axioms() == set()


@needs_lean
@pytest.mark.parametrize(
    "files, why",
    [
        ({"lean/Main.lean": MAIN}, "not in the bundle"),
        (
            {"lean/Main.lean": MAIN, "lean/Proj/Defs.lean": DEFS + "axiom evil : False\n"},
            "lean/Proj/Defs.lean: Lean file declares an `axiom`",
        ),
    ],
)
def test_multi_file_problems(tmp_path, files, why):
    got, report, log = _run(tmp_path, files, "lean/Main.lean")
    assert got == "fail" and report is not None
    assert any(why in e for e in report.errors), report.errors


CHECKER_DATA = ROOT / "worker" / "checkers" / "testdata" / "hadamard_matrix"
RUST = ROOT / "worker" / "checkers-rs" / "target" / "release" / "proofxiv-check"


@pytest.mark.skipif(
    shutil.which("bwrap") is None or not RUST.exists(), reason="needs bubblewrap and proofxiv-check"
)
@pytest.mark.parametrize(
    "vector, verdict", [("pass/paley1_12.json", "pass"), ("fail/paley12_repeated_row.json", "fail")]
)
def test_checkers_run_in_the_sandbox(tmp_path, vector, verdict):
    from proofxiv_protocol.papers import Resources
    from proofxiv_protocol.runs import RunJobSpec
    from proofxiv_worker import runner
    from proofxiv_worker.config import Config

    inputs = tmp_path / "in"
    inputs.mkdir()
    (inputs / "cert.json").write_bytes((CHECKER_DATA / vector).read_bytes())
    spec = RunJobSpec(
        job_id="00000000-0000-0000-0000-000000000002",
        kind="checker",
        image="checkers",
        command=["check-certificate", "hadamard_matrix", "cert.json"],
        inputs=[{"sha256": "0" * 64, "path": "cert.json"}],
        resources=Resources(minutes=2, ram_gb=2, cores=1),
        certificate_type="hadamard_matrix",
    )
    config = Config(sandbox="bwrap", rust_checker=str(RUST))
    got, report, log, description = runner.run_checker(config, spec, inputs)
    assert got.value == verdict, log
    assert report is not None and set(report.implementations) == {"python", "rust"}
    assert "bubblewrap" in description


def _run(tmp_path: Path, files: dict[str, str], main: str):
    from proofxiv_protocol.papers import Resources
    from proofxiv_protocol.runs import RunJobSpec
    from proofxiv_worker import runner
    from proofxiv_worker.config import Config

    inputs = tmp_path / "in"
    for path, text in files.items():
        (inputs / path).parent.mkdir(parents=True, exist_ok=True)
        (inputs / path).write_text(text)
    spec = RunJobSpec(
        job_id="00000000-0000-0000-0000-000000000001",
        kind="lean",
        image="leanprover/lean4:v4.34.1",
        command=["lean-check", main],
        inputs=[{"sha256": "0" * 64, "path": p} for p in files],
        resources=Resources(minutes=5, ram_gb=4, cores=1),
        lean_theorems=["t"],
    )
    config = Config(sandbox="bwrap", lean_toolchains={"4.34.1": str(TOOLCHAIN)})
    got, report, log, _ = runner.run_lean(config, spec, inputs)
    return got.value, report, log
