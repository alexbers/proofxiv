"""Canonical JSON (RFC 8785) and ed25519 signatures for ProofXiv bundles.

Every decisive result, run result and owner statement travels as a ``SignedBundle``:
the payload is serialized with JCS, signed with ed25519 and can be verified by anyone
who knows the public key, independently of the server.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any

import rfc8785
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey
from pydantic import BaseModel


def canonical_json(obj: Any) -> bytes:
    """Serialize JSON-compatible data per RFC 8785 (JSON Canonicalization Scheme)."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    return rfc8785.dumps(obj)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def generate_keypair() -> tuple[str, str]:
    """Return (private_key_b64, public_key_b64)."""
    key = SigningKey.generate()
    return _b64(bytes(key)), _b64(bytes(key.verify_key))


def public_key_of(private_key_b64: str) -> str:
    return _b64(bytes(SigningKey(_unb64(private_key_b64)).verify_key))


def key_fingerprint(public_key_b64: str) -> str:
    """Short stable identifier of a public key."""
    return sha256_hex(_unb64(public_key_b64))[:32]


def is_valid_public_key(public_key_b64: str) -> bool:
    try:
        VerifyKey(_unb64(public_key_b64))
    except Exception:
        return False
    return True


def sign_bytes(data: bytes, private_key_b64: str) -> str:
    return _b64(SigningKey(_unb64(private_key_b64)).sign(data).signature)


def verify_bytes(data: bytes, signature_b64: str, public_key_b64: str) -> bool:
    try:
        VerifyKey(_unb64(public_key_b64)).verify(data, _unb64(signature_b64))
    except (BadSignatureError, ValueError):
        return False
    return True


def new_nonce() -> str:
    return secrets.token_hex(16)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SignedBundle(BaseModel):
    """A payload plus an ed25519 signature over its canonical JSON."""

    payload: dict[str, Any]
    key_fingerprint: str
    signature: str

    @classmethod
    def create(cls, payload: BaseModel | dict[str, Any], private_key_b64: str) -> SignedBundle:
        data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
        return cls(
            payload=data,
            key_fingerprint=key_fingerprint(public_key_of(private_key_b64)),
            signature=sign_bytes(canonical_json(data), private_key_b64),
        )

    def payload_bytes(self) -> bytes:
        return canonical_json(self.payload)

    def payload_sha256(self) -> str:
        return sha256_hex(self.payload_bytes())

    def verify(self, public_key_b64: str) -> bool:
        if key_fingerprint(public_key_b64) != self.key_fingerprint:
            return False
        return verify_bytes(self.payload_bytes(), self.signature, public_key_b64)
