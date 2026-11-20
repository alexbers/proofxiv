"""Text prefilter for Lean files (C1); the rules live in the protocol so the runner applies
the same ones."""

from proofxiv_protocol.leanscan import (
    LeanFiles,
    declares_theorem,
    lean_version,
    local_modules,
    scan_lean,
    strip_comments,
    uses_mathlib,
)

__all__ = [
    "LeanFiles",
    "declares_theorem",
    "lean_version",
    "local_modules",
    "scan_lean",
    "strip_comments",
    "uses_mathlib",
]
