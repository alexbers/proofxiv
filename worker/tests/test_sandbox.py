"""The sandbox watches memory from outside (rlimits break Lean) and enforces the time limit."""

from __future__ import annotations

import os
import shutil
import sys

import pytest
from proofxiv_worker import sandbox

pytestmark = pytest.mark.skipif(shutil.which("bwrap") is None, reason="needs bubblewrap")
PYTHON = [os.path.realpath(sys.executable), "-I", "-c"]  # the system interpreter, visible inside


def run(tmp_path, code: str, ram_gb: float = 1.0, timeout_s: float = 30):
    return sandbox.run(
        [*PYTHON, code],
        workdir=tmp_path,
        ro_paths=[],
        timeout_s=timeout_s,
        ram_gb=ram_gb,
        backend="bwrap",
    )


def test_within_limits(tmp_path):
    result = run(tmp_path, "print('ok')")
    assert result.returncode == 0 and result.stdout.strip() == "ok"
    assert not result.out_of_memory and not result.timed_out


def test_memory_hog_is_killed(tmp_path):
    hog = "import time\nx = b'x' * (800 * 1024 * 1024)\ntime.sleep(30)"
    result = run(tmp_path, hog, ram_gb=0.25)
    assert result.out_of_memory and result.returncode == -9
    assert result.peak_memory_mb is not None and result.peak_memory_mb > 256
    assert result.duration_s < 20


def test_time_limit(tmp_path):
    result = run(tmp_path, "import time\ntime.sleep(30)", timeout_s=2)
    assert result.timed_out and not result.out_of_memory and result.duration_s < 10
