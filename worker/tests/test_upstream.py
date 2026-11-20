"""The edit formal-conjectures' CONTRIBUTING asks for when a problem is solved."""

from __future__ import annotations

import pytest
from proofxiv_worker.upstream import UpstreamError, mark_solved

FILE = """namespace OeisA60841

/-- The conjecture. -/
@[category research open, AMS 11 15]
theorem conjecture1 (n : ℕ) : n = n := by
  sorry

/-- Another one. -/
@[category research open, AMS 11]
theorem conjecture2 : True := by
  sorry

end OeisA60841
"""
PALOMAR = "https://palomar-registry.org/entry.html?id=PALOMAR-2026-11-10-000008"


def test_mark_solved_changes_only_that_statement():
    out = mark_solved(FILE, "conjecture1", PALOMAR)
    assert out.count("research solved") == 1 and out.count("research open") == 1
    assert f'@[category research solved, AMS 11 15,\n  formal_proof using lean4 at "{PALOMAR}"]\ntheorem conjecture1' in out
    assert "theorem conjecture2" in out and out.index("research open") > out.index("conjecture1")
    informal = mark_solved(FILE, "conjecture2", None)
    assert "@[category research solved, AMS 11]\ntheorem conjecture2" in informal and "formal_proof" not in informal


def test_mark_solved_refuses_what_changed_upstream():
    with pytest.raises(UpstreamError, match="not in the file"):
        mark_solved(FILE, "conjecture3", None)
    with pytest.raises(UpstreamError, match="not tagged"):
        mark_solved(mark_solved(FILE, "conjecture1", None), "conjecture1", None)
