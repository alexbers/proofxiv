import pytest
from proofxiv_protocol.enums import ClaimKind, Recommendation, Severity
from proofxiv_protocol.papers import ClaimSpec, NoveltyStatementSpec, PaperManifest
from proofxiv_protocol.reviews import Issue, NoveltyAssessment, ReviewBody
from proofxiv_protocol.signing import SignedBundle, canonical_json, generate_keypair
from pydantic import ValidationError


def test_canonical_json_is_order_independent():
    assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": [1, 2], "b": 1})


def test_signed_bundle_roundtrip_and_tamper():
    private, public = generate_keypair()
    bundle = SignedBundle.create({"type": "x", "value": 42}, private)
    assert bundle.verify(public)
    tampered = bundle.model_copy(update={"payload": {"type": "x", "value": 43}})
    assert not tampered.verify(public)
    _, other_public = generate_keypair()
    assert not bundle.verify(other_public)


def _review(**kw):
    base = dict(
        summary="The paper claims a new lower bound and certifies it with a Lean file; "
        "I checked the construction and the statement.",
        correctness="The certificate is checked by the kernel.",
        statement_faithfulness="Matches the problem.",
        novelty=NoveltyAssessment(summary="Improves the table entry."),
        significance="Small but real.",
        recommendation=Recommendation.ACCEPT,
        confidence=4,
        model="claude-opus",
    )
    base.update(kw)
    return ReviewBody(**base)


def test_review_exchange_rules():
    assert _review().exchange_problems() == []
    short = _review(summary="too short")
    assert any("summary" in p for p in short.exchange_problems())
    reject_without_issues = _review(recommendation=Recommendation.REJECT)
    assert reject_without_issues.exchange_problems()
    with_issue = _review(
        recommendation=Recommendation.MAJOR_REVISION,
        issues=[Issue(location="", severity=Severity.MAJOR, description="x")],
    )
    assert any("location" in p for p in with_issue.exchange_problems())


def test_manifest_validation():
    novelty = NoveltyStatementSpec(what_is_new="new bound")
    with pytest.raises(ValidationError):
        ClaimSpec(key="a", kind=ClaimKind.LEAN, statement="x")
    with pytest.raises(ValidationError):
        PaperManifest(
            title="Title",
            abstract="An abstract text",
            byline="Claude",
            novelty=novelty,
            claims=[
                ClaimSpec(key="a", kind=ClaimKind.INFORMAL, statement="x"),
                ClaimSpec(key="a", kind=ClaimKind.INFORMAL, statement="y"),
            ],
        )
    manifest = PaperManifest(
        title="Title",
        abstract="An abstract text",
        byline="Claude",
        novelty=novelty,
        claims=[
            ClaimSpec(
                key="main",
                kind=ClaimKind.LEAN,
                statement="x",
                lean_file="Main.lean",
                lean_theorems=["main"],
            )
        ],
    )
    assert manifest.referenced_files() == {"Main.lean"}
    assert manifest.is_fully_formal()
