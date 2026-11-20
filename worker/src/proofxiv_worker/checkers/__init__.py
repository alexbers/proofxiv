"""ProofXiv reference checkers (Python implementation).

Every certificate type is implemented twice: here and in the Rust crate ``worker/checkers-rs``
(binary ``proofxiv-check``).  A certificate is accepted only when both implementations accept it;
``run_both`` runs the two command-line implementations and compares them.  The certificate
formats are documented in ``worker/checkers/FORMATS.md``.

API::

    from proofxiv_worker.checkers import check, CHECKERS
    result = check("hadamard_matrix", {"n": 2, "rows": ["++", "+-"]})
    result.ok, result.details, result.stats
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .codes import check_covering_code, check_linear_code
from .common import (
    CertificateError,
    CheckResult,
    MalformedCertificate,
    UnknownCertificateType,
    parse_json_bytes,
)
from .geometry import check_affine_line_free, check_heilbronn, check_kissing
from .graphs import check_regular_graph_girth
from .matrices import check_circulant_weighing, check_hadamard
from .polynomial import check_polynomial
from .ramsey import check_graph_ramsey, check_hypergraph_ramsey
from .runboth import find_rust_binary, run_both
from .vdw import check_vdw

CHECKERS: dict[str, Callable[[Any], CheckResult]] = {
    "graph_ramsey_coloring": check_graph_ramsey,
    "hypergraph_ramsey_coloring": check_hypergraph_ramsey,
    "vdw_coloring": check_vdw,
    "linear_code": check_linear_code,
    "covering_code": check_covering_code,
    "regular_graph_girth": check_regular_graph_girth,
    "hadamard_matrix": check_hadamard,
    "circulant_weighing_matrix": check_circulant_weighing,
    "affine_line_free_set": check_affine_line_free,
    "kissing_configuration": check_kissing,
    "heilbronn_configuration": check_heilbronn,
    "polynomial_positive_interval": check_polynomial,
}


def check(type: str, data: Any) -> CheckResult:  # noqa: A002 - name fixed by the API contract
    """Check one certificate.

    Returns a ``CheckResult`` (``ok`` is the verdict).  Raises ``UnknownCertificateType`` for an
    unknown type and ``MalformedCertificate`` when ``data`` does not follow the documented format
    (both are ``CertificateError``; the command-line interface maps them to exit code 2).
    """
    try:
        checker = CHECKERS[type]
    except (KeyError, TypeError):
        raise UnknownCertificateType(f"unknown certificate type {type!r}") from None
    return checker(data)


__all__ = [
    "CHECKERS",
    "CertificateError",
    "CheckResult",
    "MalformedCertificate",
    "UnknownCertificateType",
    "check",
    "find_rust_binary",
    "parse_json_bytes",
    "run_both",
]
