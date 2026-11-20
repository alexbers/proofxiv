"""File names come from submissions: a download must stay inside its work directory."""

from __future__ import annotations

from pathlib import Path

import pytest
from proofxiv_worker.client import ApiError, Client
from proofxiv_worker.config import Config


def test_download_refuses_paths_outside_the_work_directory(tmp_path: Path):
    base = tmp_path / "work" / "files"
    base.mkdir(parents=True)
    with Client(Config(server="http://127.0.0.1:9"), token="") as client:
        for name in ("../../.bashrc", "/etc/passwd", "a/../../outside.txt"):
            with pytest.raises(ApiError, match="unsafe file path"):
                client.download("0" * 64, base / name, base=base)
    assert not (tmp_path / ".bashrc").exists()
