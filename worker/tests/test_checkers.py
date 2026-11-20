"""Reference checkers: both implementations (Python and Rust) on the shared test vectors.

Vectors live in ``worker/checkers/testdata/<type>/<category>/*.json``; the category gives the
expected verdict: ``pass`` (exit 0), ``fail`` (exit 1), ``malformed`` (exit 2), and the real
certificates from our research rounds ``real`` (exit 0) and ``real_fail`` (exit 1).  For every
vector both implementations must return the expected exit code and identical ``stats``.

The real-data tests are marked ``slow`` and only run when selected with ``-m slow``; they run both
command-line implementations through ``run_both`` and print the timings.

The Rust binary is built with ``cargo build --release`` (CARGO_HOME and TMPDIR inside
``proofxiv/.dev``) unless ``PROOFXIV_CHECK_BIN`` points to a prebuilt ``proofxiv-check``.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from proofxiv_worker.checkers import CHECKERS, CheckResult, UnknownCertificateType, check, run_both
from proofxiv_worker.checkers.__main__ import main as python_cli
from proofxiv_worker.checkers.common import MalformedCertificate

WORKER = Path(__file__).resolve().parents[1]
PROOFXIV = WORKER.parent
TESTDATA = WORKER / "checkers" / "testdata"
FORMATS = WORKER / "checkers" / "FORMATS.md"
CRATE = WORKER / "checkers-rs"
EXPECTED_EXIT = {"pass": 0, "fail": 1, "malformed": 2, "real": 0, "real_fail": 1}
FAST = ("pass", "fail", "malformed")
SLOW = ("real", "real_fail")


def _vectors(categories: tuple[str, ...]) -> list:
    params = []
    for path in sorted(TESTDATA.glob("*/*/*.json")):
        ctype, category = path.parent.parent.name, path.parent.name
        if category in categories:
            params.append(pytest.param(ctype, category, path, id=f"{ctype}/{category}/{path.stem}"))
    return params


@pytest.fixture(scope="session")
def rust_binary() -> str:
    explicit = os.environ.get("PROOFXIV_CHECK_BIN")
    if explicit:
        return explicit
    env = dict(os.environ)
    env.setdefault("CARGO_HOME", str(PROOFXIV / ".dev" / "cargo-home"))
    env.setdefault("TMPDIR", str(PROOFXIV / ".dev" / "tmp"))
    Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["cargo", "build", "--release", "--quiet"],
        cwd=CRATE,
        env=env,
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    assert proc.returncode == 0, f"cargo build failed:\n{proc.stderr}"
    binary = CRATE / "target" / "release" / "proofxiv-check"
    assert binary.is_file()
    return str(binary)


def run_python(ctype: str, path: Path) -> tuple[int, dict]:
    """The Python command-line entry point, in process (same code path as python -m ...)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = python_cli([ctype, str(path)])
    lines = out.getvalue().splitlines()
    assert len(lines) == 1, out.getvalue()
    return code, json.loads(lines[0])


def run_rust(binary: str, ctype: str, path: Path) -> tuple[int, dict]:
    proc = subprocess.run([binary, ctype, str(path)], capture_output=True, timeout=600, check=False)
    lines = proc.stdout.decode().splitlines()
    assert len(lines) == 1, proc.stdout + proc.stderr
    return proc.returncode, json.loads(lines[0])


def _assert_line(result: dict, ctype: str, code: int) -> None:
    assert set(result) == {"ok", "type", "details", "stats"}
    assert result["type"] == ctype
    assert result["ok"] is (code == 0)
    assert isinstance(result["details"], str) and result["details"]
    assert isinstance(result["stats"], dict)


# ---------------------------------------------------------------------------------------------
# hand-made vectors: expected verdicts and agreement
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ctype,category,path", _vectors(FAST))
def test_vector(rust_binary: str, ctype: str, category: str, path: Path) -> None:
    expected = EXPECTED_EXIT[category]
    py_code, py = run_python(ctype, path)
    rs_code, rs = run_rust(rust_binary, ctype, path)
    _assert_line(py, ctype, py_code)
    _assert_line(rs, ctype, rs_code)
    assert (py_code, rs_code) == (expected, expected), f"python: {py}\nrust: {rs}"
    if expected != 2:
        assert py["stats"] == rs["stats"], (
            f"stats differ:\npython: {py['stats']}\nrust: {rs['stats']}"
        )


def test_documented_examples(rust_binary: str, tmp_path: Path) -> None:
    """Every JSON example in FORMATS.md is a valid certificate for both implementations."""
    blocks = re.findall(r"```json\n(.*?)```", FORMATS.read_text(), flags=re.S)
    seen = set()
    for i, block in enumerate(blocks):
        doc = json.loads(block)
        if "ok" in doc:  # the example of the command-line output, not a certificate
            continue
        path = tmp_path / f"example_{i}.json"
        path.write_text(block)
        py_code, py = run_python(doc["type"], path)
        rs_code, rs = run_rust(rust_binary, doc["type"], path)
        assert (py_code, rs_code) == (0, 0), (block, py, rs)
        assert py["stats"] == rs["stats"]
        seen.add(doc["type"])
    assert seen == set(CHECKERS)


def test_every_type_has_vectors() -> None:
    documented = FORMATS.read_text()
    for ctype in CHECKERS:
        for category, minimum in (("pass", 3), ("fail", 3), ("malformed", 3)):
            found = list((TESTDATA / ctype / category).glob("*.json"))
            assert len(found) >= minimum, f"{ctype}/{category}: {len(found)} vectors"
        assert list((TESTDATA / ctype / "real").glob("*.json")), f"{ctype}: no real-data vector"
        assert f"`{ctype}`" in documented, f"{ctype} is not documented in FORMATS.md"


# ---------------------------------------------------------------------------------------------
# API and command-line contract
# ---------------------------------------------------------------------------------------------


def test_api() -> None:
    assert len(CHECKERS) == 12
    result = check("hadamard_matrix", {"n": 2, "rows": ["++", "+-"]})
    assert (
        isinstance(result, CheckResult)
        and result.ok
        and result.stats == {"n": 2, "symmetric": True}
    )
    assert not check("hadamard_matrix", {"n": 2, "rows": ["++", "++"]}).ok
    with pytest.raises(UnknownCertificateType):
        check("no_such_type", {})
    with pytest.raises(MalformedCertificate):
        check("hadamard_matrix", {"n": 2, "rows": ["++", "+-"], "extra": 1})
    with pytest.raises(MalformedCertificate):
        check("hadamard_matrix", {"n": True, "rows": ["+"]})  # booleans are not integers


def test_cli_errors(rust_binary: str) -> None:
    vector = next(TESTDATA.glob("hadamard_matrix/pass/*.json"))
    missing = PROOFXIV / ".dev" / "tmp" / "no-such-certificate.json"
    for args in (
        ["no_such_type", str(vector)],
        ["hadamard_matrix", str(missing)],
        ["hadamard_matrix"],
    ):
        py = subprocess.run(
            [sys.executable, "-m", "proofxiv_worker.checkers", *args], capture_output=True, check=False
        )
        rs = subprocess.run([rust_binary, *args], capture_output=True, check=False)
        assert py.returncode == rs.returncode == 2, (args, py.stdout, rs.stdout)
        for proc in (py, rs):
            line = json.loads(proc.stdout.decode().strip())
            assert line["ok"] is False and line["stats"] == {}


def test_python_cli_subprocess() -> None:
    """The real `python -m proofxiv_worker.checkers` entry point, one vector per category."""
    for category, expected in (("pass", 0), ("fail", 1), ("malformed", 2)):
        vector = sorted(TESTDATA.glob(f"polynomial_positive_interval/{category}/*.json"))[0]
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "proofxiv_worker.checkers",
                "polynomial_positive_interval",
                str(vector),
            ],
            capture_output=True,
            check=False,
        )
        assert proc.returncode == expected, proc.stdout
        assert len(proc.stdout.decode().splitlines()) == 1


@pytest.mark.parametrize("ctype", sorted(CHECKERS))
def test_run_both(rust_binary: str, ctype: str) -> None:
    good = sorted((TESTDATA / ctype / "pass").glob("*.json"))[0]
    outcome = run_both(ctype, good, rust_binary)
    assert outcome["implementations"] == {"python": True, "rust": True}, outcome["details"]
    assert outcome["agree"] and outcome["ok"]
    bad = sorted((TESTDATA / ctype / "fail").glob("*.json"))[0]
    outcome = run_both(ctype, bad, rust_binary)
    assert outcome["implementations"] == {"python": False, "rust": False}
    assert outcome["agree"] and not outcome["ok"]


def test_run_both_without_rust(monkeypatch: pytest.MonkeyPatch) -> None:
    import proofxiv_worker.checkers.runboth as rb

    monkeypatch.setattr(rb, "find_rust_binary", lambda explicit=None: None)
    vector = sorted(TESTDATA.glob("vdw_coloring/pass/*.json"))[0]
    outcome = rb.run_both("vdw_coloring", vector, None)
    assert outcome["implementations"] == {"python": True, "rust": False}
    assert not outcome["agree"] and not outcome["ok"]
    assert "not found" in outcome["details"]


# ---------------------------------------------------------------------------------------------
# real certificates from the research rounds (slow)
# ---------------------------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("ctype,category,path", _vectors(SLOW))
def test_real_certificate(
    rust_binary: str,
    ctype: str,
    category: str,
    path: Path,
    request: pytest.FixtureRequest,
    record_property,
) -> None:
    if "slow" not in (request.config.getoption("markexpr") or ""):
        pytest.skip("real-data test: select with -m slow")
    expected = EXPECTED_EXIT[category]
    outcome = run_both(ctype, path, rust_binary)
    seconds = outcome["seconds"]
    record_property("seconds", seconds)
    print(
        f"\nTIMING {ctype}/{path.stem}: python {seconds['python']:.2f} s, rust {seconds['rust']:.2f} s"
    )
    assert outcome["exit_codes"] == {"python": expected, "rust": expected}, outcome["details"]
    assert outcome["agree"], outcome["details"]
    assert outcome["ok"] is (expected == 0)
