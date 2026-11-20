"""Command line: ``python -m proofxiv_worker.checkers <certificate_type> <file.json>``.

Prints exactly one JSON line ``{"ok", "type", "details", "stats"}`` and exits with 0 (valid),
1 (invalid) or 2 (malformed input, unknown type, unreadable file or internal error).
"""

from __future__ import annotations

import json
import sys
import traceback

from . import CHECKERS, check
from .common import CertificateError, parse_json_bytes


def _emit(ok: bool, ctype: str, details: str, stats: dict) -> None:
    line = json.dumps(
        {"ok": ok, "type": ctype, "details": details, "stats": stats},
        ensure_ascii=True,
        separators=(", ", ": "),
    )
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(0)  # big integers are converted in chunks anyway; no limit needed
    try:
        return _main(sys.argv[1:] if argv is None else argv)
    finally:
        sys.set_int_max_str_digits(previous)


def _main(args: list[str]) -> int:
    if len(args) != 2:
        _emit(
            False, "", "usage: python -m proofxiv_worker.checkers <certificate_type> <file.json>", {}
        )
        return 2
    ctype, path = args
    if ctype not in CHECKERS:
        _emit(False, ctype, f"unknown certificate type {ctype!r}; known: {', '.join(CHECKERS)}", {})
        return 2
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        _emit(False, ctype, f"cannot read {path}: {exc.strerror or exc}", {})
        return 2
    try:
        data = parse_json_bytes(raw)
        result = check(ctype, data)
    except CertificateError as exc:
        _emit(False, ctype, f"malformed certificate: {exc}", {})
        return 2
    except (MemoryError, RecursionError) as exc:
        _emit(False, ctype, f"resource limit exceeded: {type(exc).__name__}", {})
        return 2
    except Exception as exc:  # an internal error must never look like a verdict
        tb = traceback.format_exc(limit=3).replace("\n", " | ")
        _emit(False, ctype, f"internal error: {type(exc).__name__}: {exc} [{tb}]", {})
        return 2
    _emit(bool(result.ok), ctype, result.details, result.stats)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
