"""Shared infrastructure of the Python reference checkers.

Everything here is exact: integers, Python big integers and ``fractions.Fraction``.  No floating
point value ever influences a verdict.  The JSON conventions implemented here are documented in
``worker/checkers/FORMATS.md`` (section "Common conventions") and are mirrored by the Rust
implementation in ``worker/checkers-rs``.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from fractions import Fraction
from math import comb
from typing import Any

I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
MAX_JSON_DEPTH = 64
MAX_DIGITS = 10_000  # longest decimal integer accepted inside a string
GENERIC_FIELDS = ("type", "comment")

_INT_RE = re.compile(r"-?[0-9]+")
_RAT_RE = re.compile(r"(-?[0-9]+)(?:/([0-9]+))?")


class CertificateError(ValueError):
    """Base class: the input cannot be judged (exit code 2)."""


class MalformedCertificate(CertificateError):
    """The certificate does not follow its documented format (exit code 2)."""


class UnknownCertificateType(CertificateError):
    """The certificate type is not one of ``CHECKERS`` (exit code 2)."""


@dataclass
class CheckResult:
    ok: bool
    details: str
    stats: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------
# Big integers <-> decimal text without CPython's 4300-digit conversion limit
# ---------------------------------------------------------------------------------------------

_CHUNK = 1000
_TEN_CHUNK = 10**_CHUNK


def decimal_to_int(digits: str) -> int:
    """Parse ``-?[0-9]+`` (already validated) of any length up to MAX_DIGITS."""
    neg = digits.startswith("-")
    body = digits[1:] if neg else digits
    value = 0
    head = len(body) % _CHUNK or _CHUNK
    value = int(body[:head])
    for start in range(head, len(body), _CHUNK):
        value = value * _TEN_CHUNK + int(body[start : start + _CHUNK])
    return -value if neg else value


def big_str(value: int) -> str:
    """Decimal representation of an integer of any size."""
    if -(10**4000) < value < 10**4000:
        return str(value)
    neg = value < 0
    value = -value if neg else value
    chunks = []
    while value:
        value, low = divmod(value, _TEN_CHUNK)
        chunks.append(low)
    text = str(chunks[-1]) + "".join(str(c).zfill(_CHUNK) for c in reversed(chunks[:-1]))
    return "-" + text if neg else text


def fraction_str(x: Fraction) -> str:
    if x.denominator == 1:
        return big_str(x.numerator)
    return f"{big_str(x.numerator)}/{big_str(x.denominator)}"


def abbreviate(text: str, limit: int = 60) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit // 2]}...{text[-limit // 2 :]} ({len(text)} chars)"


# ---------------------------------------------------------------------------------------------
# Strict JSON parsing
# ---------------------------------------------------------------------------------------------


def _reject_float(text: str) -> Any:
    raise MalformedCertificate(f"non-integer JSON number {abbreviate(text)!r} is not allowed")


def _reject_constant(text: str) -> Any:
    raise MalformedCertificate(f"JSON constant {text!r} is not allowed")


def _parse_int(text: str) -> int:
    if len(text) > 21:
        raise MalformedCertificate("JSON integer literal out of the signed 64-bit range")
    if text.startswith("-") and text.strip("-0") == "":
        raise MalformedCertificate("the JSON number -0 is not allowed")
    value = int(text)
    if not I64_MIN <= value <= I64_MAX:
        raise MalformedCertificate("JSON integer literal out of the signed 64-bit range")
    return value


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise MalformedCertificate(f"duplicate key {key!r} in a JSON object")
        out[key] = value
    return out


def _check_string(text: str) -> None:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise MalformedCertificate("JSON string contains an unpaired surrogate") from exc


def _depth(value: Any) -> int:
    """Nesting depth: scalars 0, a container 1 + the depth of its deepest element."""
    if isinstance(value, str):
        _check_string(value)
        return 0
    if isinstance(value, list):
        return 1 + max((_depth(x) for x in value), default=0)
    if isinstance(value, dict):
        for key in value:
            _check_string(key)
        return 1 + max((_depth(x) for x in value.values()), default=0)
    return 0


def parse_json_bytes(raw: bytes) -> Any:
    """Parse a certificate file strictly (see FORMATS.md, "Common conventions")."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MalformedCertificate("file is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            parse_float=_reject_float,
            parse_int=_parse_int,
            parse_constant=_reject_constant,
            object_pairs_hook=_object_pairs,
        )
    except MalformedCertificate:
        raise
    except (ValueError, RecursionError) as exc:
        raise MalformedCertificate(f"invalid JSON: {abbreviate(str(exc), 200)}") from exc
    try:
        depth = _depth(value)
    except RecursionError as exc:
        raise MalformedCertificate(f"JSON nesting deeper than {MAX_JSON_DEPTH}") from exc
    if depth > MAX_JSON_DEPTH:
        raise MalformedCertificate(f"JSON nesting deeper than {MAX_JSON_DEPTH}")
    return value


# ---------------------------------------------------------------------------------------------
# Field access helpers
# ---------------------------------------------------------------------------------------------


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check_fields(
    data: Any, ctype: str, required: Iterable[str], optional: Iterable[str] = ()
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise MalformedCertificate("the certificate must be a JSON object")
    required = tuple(required)
    allowed = set(required) | set(optional) | set(GENERIC_FIELDS)
    for key in data:
        if key not in allowed:
            raise MalformedCertificate(f"unknown field {key!r}")
    for key in required:
        if key not in data:
            raise MalformedCertificate(f"missing field {key!r}")
    if "type" in data and data["type"] != ctype:
        raise MalformedCertificate(f"field 'type' is {data['type']!r}, expected {ctype!r}")
    if "comment" in data and not isinstance(data["comment"], str):
        raise MalformedCertificate("field 'comment' must be a string")
    return data


def as_int(value: Any, what: str, lo: int | None = None, hi: int | None = None) -> int:
    if not is_int(value):
        raise MalformedCertificate(f"{what} must be an integer")
    if not I64_MIN <= value <= I64_MAX:
        raise MalformedCertificate(f"{what} is out of the signed 64-bit range")
    if lo is not None and value < lo:
        raise MalformedCertificate(f"{what} must be >= {lo}")
    if hi is not None and value > hi:
        raise MalformedCertificate(f"{what} must be <= {hi}")
    return value


def get_int(data: dict[str, Any], key: str, lo: int | None = None, hi: int | None = None) -> int:
    return as_int(data[key], f"field {key!r}", lo, hi)


def get_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, bool):
        raise MalformedCertificate(f"field {key!r} must be true or false")
    return value


def get_str(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise MalformedCertificate(f"field {key!r} must be a string")
    return value


def get_list(data: dict[str, Any], key: str, length: int | None = None) -> list[Any]:
    value = data[key]
    if not isinstance(value, list):
        raise MalformedCertificate(f"field {key!r} must be an array")
    if length is not None and len(value) != length:
        raise MalformedCertificate(f"field {key!r} must have length {length}, got {len(value)}")
    return value


def int_list(
    value: Any, what: str, length: int | None = None, lo: int | None = None, hi: int | None = None
) -> list[int]:
    if not isinstance(value, list):
        raise MalformedCertificate(f"{what} must be an array")
    if length is not None and len(value) != length:
        raise MalformedCertificate(f"{what} must have length {length}, got {len(value)}")
    for i, item in enumerate(value):
        as_int(item, f"{what}[{i}]", lo, hi)
    return value


def _digits_ok(text: str) -> bool:
    return len(text.lstrip("-")) <= MAX_DIGITS


def parse_big_int(value: Any, what: str) -> int:
    """An arbitrary-size integer: a JSON integer or a decimal string ``-?[0-9]+``."""
    if is_int(value):
        return as_int(value, what)
    if (
        isinstance(value, str)
        and len(value) <= MAX_DIGITS + 1
        and _INT_RE.fullmatch(value)
        and _digits_ok(value)
    ):
        return decimal_to_int(value)
    raise MalformedCertificate(
        f"{what} must be an integer or a decimal integer string of at most {MAX_DIGITS} digits"
    )


def parse_rational(value: Any, what: str) -> Fraction:
    """A rational number: a JSON integer, or a string ``-?[0-9]+`` or ``-?[0-9]+/[0-9]+``."""
    if is_int(value):
        return Fraction(as_int(value, what))
    if isinstance(value, str) and len(value) <= 2 * MAX_DIGITS + 2:
        m = _RAT_RE.fullmatch(value)
        if (
            m is not None
            and _digits_ok(m.group(1))
            and (m.group(2) is None or _digits_ok(m.group(2)))
        ):
            num = decimal_to_int(m.group(1))
            den = decimal_to_int(m.group(2)) if m.group(2) is not None else 1
            if den == 0:
                raise MalformedCertificate(f"{what} has a zero denominator")
            return Fraction(num, den)
    raise MalformedCertificate(
        f"{what} must be an integer or a string 'p' or 'p/q' (at most {MAX_DIGITS} digits each)"
    )


def decode_base64_strict(text: Any, what: str) -> bytes:
    """Standard base64 (RFC 4648 alphabet, '=' padding), canonical form only."""
    if not isinstance(text, str) or not text.isascii():
        raise MalformedCertificate(f"{what} must be an ASCII base64 string")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MalformedCertificate(f"{what} is not valid base64: {exc}") from exc
    if base64.b64encode(raw).decode("ascii") != text:
        raise MalformedCertificate(f"{what} is not in canonical base64 form")
    return raw


def binom(n: int, k: int) -> int:
    if k < 0 or n < 0 or k > n:
        return 0
    return comb(n, k)


def short_list(items: Iterable[Any], limit: int = 12) -> str:
    items = list(items)
    if len(items) <= limit:
        return "[" + ", ".join(str(x) for x in items) + "]"
    return "[" + ", ".join(str(x) for x in items[:limit]) + f", ... ({len(items)} items)]"
