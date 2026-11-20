"""The formal-conjectures parser takes only `research open` theorems, one docstring each."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from proofxiv_protocol.enums import ProblemStatus
from proofxiv_protocol.problems import ProblemProposal, SourceRef
from proofxiv_server.importers.formal_conjectures import head_commit, in_domains, parse_file

from .conftest import ROOT, agent, login

SAMPLE = '''/-!
# Erdős Problem 9999

*Reference:* [erdosproblems.com/9999](https://www.erdosproblems.com/9999)
-/

/-- A helper definition; its docstring must not leak into the next problem. -/
abbrev IsNice (n : ℕ) : Prop := n % 2 = 0

/--
Is every nice number the sum of two primes?
-/
@[category research open, AMS 11]
theorem erdos_9999 : ∀ n, IsNice n → 4 ≤ n → ∃ p q, p.Prime ∧ q.Prime ∧ n = p + q := by
  sorry

/-- Solved variant. -/
@[category research solved, AMS 11]
theorem erdos_9999.variants.small : IsNice 4 := by
  sorry
'''


def test_parse_formal_conjectures_file(tmp_path: Path):
    path = tmp_path / "FormalConjectures" / "ErdosProblems" / "9999.lean"
    path.parent.mkdir(parents=True)
    path.write_text(SAMPLE)
    found = parse_file(path, tmp_path, "0" * 40)
    assert [(p.title, p.variant) for p in found] == [("Erdős Problem 9999", "erdos_9999")]
    problem = found[0]
    assert problem.statement_md == "Is every nice number the sum of two primes?"
    assert problem.domain == "number theory"
    assert problem.lean_statement.startswith("theorem erdos_9999 : ∀ n")
    assert problem.sources[0].url == "https://www.erdosproblems.com/9999"
    assert "FormalConjectures/ErdosProblems/9999.lean" in problem.sources[1].url


def test_a_lab_imports_only_its_areas(tmp_path: Path):
    path = tmp_path / "FormalConjectures" / "ErdosProblems" / "9999.lean"
    path.parent.mkdir(parents=True)
    path.write_text(SAMPLE)
    found = parse_file(path, tmp_path, "0" * 40)
    assert in_domains(found, ["Number Theory"]) == found
    assert in_domains(found, ["combinatorics"]) == []


def test_head_commit_without_git(tmp_path: Path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "packed-refs").write_text(f"# pack-refs with: peeled\n{'a' * 40} refs/heads/main\n^{'b' * 40}\n")
    assert head_commit(tmp_path) == "a" * 40  # a fresh clone keeps its branch in packed-refs
    (git / "refs" / "heads" / "main").write_text("c" * 40 + "\n")
    assert head_commit(tmp_path) == "c" * 40
    (git / "HEAD").write_text("d" * 40 + "\n")  # detached
    assert head_commit(tmp_path) == "d" * 40


def test_migration_splits_imported_titles():
    path = ROOT / "server" / "migrations" / "versions" / "f1b83d5c7a24_problem_variant.py"
    spec = importlib.util.spec_from_file_location("problem_variant_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    split = migration.split_title

    assert split("Erdős Problem 601: erdos_601", "theorem erdos_601 :\n  ∀ α, P α := by\n  sorry") == (
        "Erdős Problem 601",
        "erdos_601",
    )
    # a colon in the title itself, a name followed by a colon, a file added to tell names apart
    assert split("Open Quantum Problem 23: SIC-POVMs: sicPOVMs", "theorem sicPOVMs (d : ℕ) : P d := by sorry") == (
        "Open Quantum Problem 23: SIC-POVMs",
        "sicPOVMs",
    )
    assert split("Erdős Problem 36: erdos_36.variants.lower", "theorem erdos_36.variants.lower:\n ∃ c, c > 0") == (
        "Erdős Problem 36",
        "erdos_36.variants.lower",
    )
    assert split("Zeros: simple_lt (FormalConjectures/Paper/Zeros.lean)", "theorem simple_lt : P := by sorry") == (
        "Zeros",
        "simple_lt (FormalConjectures/Paper/Zeros.lean)",
    )
    # nothing to split: a proposal without a Lean statement, a title that is not built from the name
    assert split("Cap set capacity: lower bounds", None) is None
    assert split("Kissing number in dimension 19", "theorem kissing19 : P := by sorry") is None


async def test_variants_share_a_title_and_are_searchable(transport):
    from proofxiv_server.core.problems import create_problem
    from proofxiv_server.db.session import sessionmaker
    from proofxiv_server.seed import import_proposals

    def proposal(variant: str) -> ProblemProposal:
        return ProblemProposal(
            title="Zarankiewicz-type problem 4242",
            variant=variant,
            statement_md=f"A statement about the variant {variant} of the problem.",
            domain="combinatorics",
            sources=[SourceRef(url="https://example.org/z4242")],
            openness_evidence="Listed as open in the test catalog.",
        )

    async with sessionmaker()() as s:
        first = await create_problem(s, proposal("z4242.parts.i"), None, ProblemStatus.OPEN, "import")
        await s.commit()
        assert (first.title, first.variant) == ("Zarankiewicz-type problem 4242", "z4242.parts.i")
        assert first.slug == "zarankiewicz-type-problem-4242-z4242-parts-i"
    # a re-import adds only the variant that is missing
    created = await import_proposals(
        [proposal("z4242.parts.i"), proposal("z4242.parts.ii")], ProblemStatus.OPEN, origin="import"
    )
    assert created == 1

    reader = await agent(transport, await login(transport, "vera"), "vera-agent")
    by_title = await reader.get("/problems", params={"q": "Zarankiewicz-type problem 4242"})
    assert sorted(p["variant"] for p in by_title if p["title"] == "Zarankiewicz-type problem 4242") == [
        "z4242.parts.i",
        "z4242.parts.ii",
    ]
    by_variant = await reader.get("/problems", params={"q": "z4242.parts.ii"})
    assert by_variant[0]["variant"] == "z4242.parts.ii"
    page = await reader.get(f"/problems/{first.slug}")
    assert page["title"] == "Zarankiewicz-type problem 4242" and page["variant"] == "z4242.parts.i"
    # the page names the other entries of the same problem, so settling one part is not read as the whole
    assert [(m["variant"], m["status"]) for m in page["family"]] == [("z4242.parts.ii", "open")]
    assert page["part_of"] is None

    case = proposal("n = 7").model_copy(
        update={"title": "Problem 4243, the case n = 7", "variant": None, "part_of": "Problem 4243 for every n; false in general."}
    )
    async with sessionmaker()() as s:
        single = await create_problem(s, case, None, ProblemStatus.OPEN, "import")
        await s.commit()
    page = await reader.get(f"/problems/{single.slug}")
    assert page["part_of"] == "Problem 4243 for every n; false in general." and page["family"] == []
