"""Signing keys stored as files with 0600 permissions under $PROOFXIV_HOME/keys. A key can be
locked with a passphrase (`.key.enc`): the owner key should be, since it signs the root
statements and the machine that keeps it also runs agents that read other people's papers."""

from __future__ import annotations

import base64
import getpass
import json
import os
import sys
from pathlib import Path

import nacl.exceptions
import nacl.pwhash
import nacl.secret
import nacl.utils
from proofxiv_protocol.signing import generate_keypair, key_fingerprint, public_key_of

from .config import home


def new_key(label: str = "worker") -> tuple[Path, str, str]:
    """Create a key file; return (path, public_key, fingerprint)."""
    private, public = generate_keypair()
    fingerprint = key_fingerprint(public)
    directory = home() / "keys"
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / f"{label}-{fingerprint[:12]}.key"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(private + "\n")
    return path, public, fingerprint


LOCKED_FORMAT = "proofxiv-locked-key-v1"
PASSPHRASE_ENV = "PROOFXIV_KEY_PASSPHRASE"


def _box(passphrase: str, salt: bytes, ops: int, mem: int) -> nacl.secret.SecretBox:
    key = nacl.pwhash.argon2id.kdf(nacl.secret.SecretBox.KEY_SIZE, passphrase.encode(), salt, opslimit=ops, memlimit=mem)
    return nacl.secret.SecretBox(key)


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def lock(path: str | Path, passphrase: str) -> Path:
    """Encrypt a key file with a passphrase (argon2id + XSalsa20-Poly1305) into <path>.enc, then
    overwrite and delete the plain file."""
    path = Path(path)
    private = load_private(path)
    salt = nacl.utils.random(nacl.pwhash.argon2id.SALTBYTES)
    ops, mem = nacl.pwhash.argon2id.OPSLIMIT_MODERATE, nacl.pwhash.argon2id.MEMLIMIT_MODERATE
    sealed = _box(passphrase, salt, ops, mem).encrypt(private.encode())
    target = path.with_name(path.name + ".enc")
    doc = {
        "format": LOCKED_FORMAT,
        "public_key": public_key_of(private),
        "salt": base64.b64encode(salt).decode(),
        "opslimit": ops,
        "memlimit": mem,
        "sealed": base64.b64encode(bytes(sealed)).decode(),
    }
    _write_private(target, json.dumps(doc, indent=2) + "\n")
    if _unlock(target, passphrase) != private:
        target.unlink()
        raise RuntimeError("the locked key does not open; the plain key was kept")
    with path.open("r+") as fh:  # overwrite before deleting
        fh.write("0" * len(fh.read()))
    path.unlink()
    return target


def _unlock(path: Path, passphrase: str) -> str:
    doc = json.loads(path.read_text())
    if doc.get("format") != LOCKED_FORMAT:
        raise ValueError(f"{path} is not a locked ProofXiv key")
    box = _box(passphrase, base64.b64decode(doc["salt"]), int(doc["opslimit"]), int(doc["memlimit"]))
    try:
        return box.decrypt(base64.b64decode(doc["sealed"])).decode()
    except nacl.exceptions.CryptoError as exc:
        raise PermissionError(f"wrong passphrase for {path.name}") from exc


def passphrase_for(path: Path) -> str:
    if os.environ.get(PASSPHRASE_ENV):
        return os.environ[PASSPHRASE_ENV]
    if not sys.stdin.isatty():
        raise PermissionError(f"{path.name} is locked: run this in a terminal (or set {PASSPHRASE_ENV})")
    return getpass.getpass(f"Passphrase for {path.name}: ")


def load_private(path: str | Path) -> str:
    path = Path(path)
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError(f"{path} must not be readable by others (chmod 600)")
    if path.name.endswith(".enc"):
        return _unlock(path, passphrase_for(path))
    return path.read_text().strip()


def public_of_file(path: str | Path) -> str:
    return public_key_of(load_private(path))
