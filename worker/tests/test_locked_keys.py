"""A key locked with a passphrase: useless without it, the plain file is gone."""

from __future__ import annotations

from pathlib import Path

import pytest
from proofxiv_protocol.signing import public_key_of
from proofxiv_worker import keys


def test_lock_and_unlock(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PROOFXIV_HOME", str(tmp_path))
    path, public, _ = keys.new_key("owner")
    private = keys.load_private(path)
    locked = keys.lock(path, "correct horse battery staple")
    assert not path.exists() and locked.name.endswith(".key.enc")
    assert locked.stat().st_mode & 0o777 == 0o600
    assert private not in locked.read_text()

    monkeypatch.setenv(keys.PASSPHRASE_ENV, "correct horse battery staple")
    assert keys.load_private(locked) == private and public_key_of(private) == public
    monkeypatch.setenv(keys.PASSPHRASE_ENV, "wrong passphrase, twelve+")
    with pytest.raises(PermissionError, match="wrong passphrase"):
        keys.load_private(locked)
    monkeypatch.delenv(keys.PASSPHRASE_ENV)
    with pytest.raises(PermissionError, match="is locked"):  # no terminal here, no passphrase
        keys.load_private(locked)
