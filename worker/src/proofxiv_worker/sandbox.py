"""Run untrusted commands in a sandbox: no network, read-only inputs, time and memory limits.

Backends:
- bwrap  — bubblewrap user namespaces (default on Linux);
- podman — rootless container (gVisor `runsc` runtime if installed);
- none   — no isolation; only for trusted local testing, must be requested explicitly.
"""

from __future__ import annotations

import contextlib
import os
import resource
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SYSTEM_RO = ["/usr", "/bin", "/lib", "/lib64", "/sbin", "/etc/alternatives", "/etc/ld.so.cache"]


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    peak_memory_mb: int | None  # None when it cannot be attributed to this run
    timed_out: bool
    out_of_memory: bool = False  # killed for using more than its memory


class SandboxUnavailable(RuntimeError):
    pass


def _session() -> None:
    # Memory is watched from outside (_group_rss): Lean reserves hundreds of gigabytes of
    # address space and writable ranges, so RLIMIT_AS and RLIMIT_DATA break honest runs.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.setsid()


WATCH_INTERVAL_S = 1.0


def _process_tree(root: int) -> dict[int, int]:
    """{pid: resident pages} for root and all its descendants (by parent pid: bubblewrap puts
    the command in its own session and PID namespace, so process groups do not reach it)."""
    parent: dict[int, int] = {}
    for entry in os.scandir("/proc"):
        if entry.name.isdigit():
            try:
                with open(f"/proc/{entry.name}/stat", "rb") as f:
                    parent[int(entry.name)] = int(f.read().rsplit(b")", 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue  # ended meanwhile
    children: dict[int, list[int]] = {}
    for pid, ppid in parent.items():
        children.setdefault(ppid, []).append(pid)
    tree: dict[int, int] = {}
    stack = [root]
    while stack:
        pid = stack.pop()
        try:
            with open(f"/proc/{pid}/statm", "rb") as f:
                tree[pid] = int(f.read().split()[1])
        except (OSError, IndexError, ValueError):
            tree[pid] = 0
        stack += children.get(pid, [])
    return tree


def _rss_mb(tree: dict[int, int]) -> int:
    return sum(tree.values()) * os.sysconf("SC_PAGE_SIZE") // (1024 * 1024)


def _kill_tree(root: int) -> None:
    for pid in sorted(_process_tree(root), key=lambda p: p != root):
        with contextlib.suppress(OSError):  # already gone
            os.kill(pid, signal.SIGKILL)


_PID_NAMESPACE: bool | None = None


def _system_binds() -> list[str]:
    cmd: list[str] = []
    for path in SYSTEM_RO:
        if os.path.exists(path):
            cmd += ["--ro-bind", path, path]
    return cmd


def pid_namespace_supported() -> bool:
    """Full isolation needs a private PID namespace with its own /proc. Some hosts (nested
    sandboxes, containers) forbid mounting proc; then we keep the host /proc read-only."""
    global _PID_NAMESPACE
    if _PID_NAMESPACE is None:
        probe = ["bwrap", "--unshare-all", "--die-with-parent", *_system_binds(),
                 "--proc", "/proc", "--dev", "/dev", "/usr/bin/true"]
        try:
            _PID_NAMESPACE = subprocess.run(probe, capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _PID_NAMESPACE = False
    return _PID_NAMESPACE


def _bwrap_prefix(workdir: Path, ro_paths: list[Path]) -> list[str]:
    if shutil.which("bwrap") is None:
        raise SandboxUnavailable("bubblewrap (bwrap) is not installed")
    if pid_namespace_supported():
        cmd = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", *_system_binds()]
        cmd += ["--proc", "/proc"]
    else:
        cmd = ["bwrap", "--unshare-net", "--unshare-ipc", "--unshare-uts", "--unshare-user",
               "--die-with-parent", "--new-session", *_system_binds(), "--ro-bind", "/proc", "/proc"]
    cmd += ["--dev", "/dev", "--tmpfs", "/tmp"]
    for path in ro_paths:
        cmd += ["--ro-bind", str(path), str(path)]
    cmd += ["--bind", str(workdir), str(workdir), "--chdir", str(workdir)]
    cmd += ["--clearenv", "--setenv", "HOME", str(workdir), "--setenv", "TMPDIR", "/tmp"]
    cmd += ["--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LANG", "C.UTF-8"]
    return cmd


def _podman_prefix(workdir: Path, ro_paths: list[Path], image: str, ram_gb: float, cores: int) -> list[str]:
    exe = shutil.which("podman") or shutil.which("docker")
    if exe is None:
        raise SandboxUnavailable("podman/docker not installed")
    cmd = [exe, "run", "--rm", "--network=none", "--read-only", "--tmpfs", "/tmp",
           f"--memory={int(ram_gb * 1024)}m", f"--cpus={cores}", "--security-opt=no-new-privileges"]
    if shutil.which("runsc"):
        cmd.append("--runtime=runsc")
    for path in ro_paths:
        cmd += ["-v", f"{path}:{path}:ro"]
    cmd += ["-v", f"{workdir}:{workdir}", "-w", str(workdir), image]
    return cmd


def run(
    command: list[str],
    workdir: Path,
    ro_paths: list[Path],
    timeout_s: float,
    ram_gb: float,
    cores: int = 2,
    backend: str = "bwrap",
    image: str | None = None,
    env: dict[str, str] | None = None,
) -> SandboxResult:
    workdir = workdir.resolve()
    ro = [p.resolve() for p in ro_paths if p.exists()]
    if backend == "bwrap":
        full = _bwrap_prefix(workdir, ro)
        for key, value in (env or {}).items():
            full += ["--setenv", key, value]
        full += command
        run_env = None
    elif backend == "podman":
        if image is None:
            raise SandboxUnavailable("podman backend needs an image")
        full = _podman_prefix(workdir, ro, image, ram_gb, cores) + command
        run_env = None
    elif backend == "none":
        full = command
        run_env = {**os.environ, **(env or {})}
    else:
        raise SandboxUnavailable(f"unknown sandbox backend '{backend}'")

    started = time.monotonic()
    deadline = started + timeout_s
    limit_mb = int(ram_gb * 1024)
    timed_out = out_of_memory = False
    peak_mb = 0
    proc = subprocess.Popen(
        full,
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        preexec_fn=_session,
        env=run_env,
    )
    while True:
        try:
            out, err = proc.communicate(timeout=WATCH_INTERVAL_S)
            break
        except subprocess.TimeoutExpired:
            used = _rss_mb(_process_tree(proc.pid))
            peak_mb = max(peak_mb, used)
            if used > limit_mb:
                out_of_memory = True
            elif time.monotonic() > deadline:
                timed_out = True
            else:
                continue
            _kill_tree(proc.pid)
            out, err = proc.communicate()
            break
    if out_of_memory:
        err += f"\n[sandbox: killed at {peak_mb} MB, over the job's {limit_mb} MB: out of memory]"
    return SandboxResult(
        returncode=-9 if (timed_out or out_of_memory) else proc.returncode,
        stdout=out,
        stderr=err,
        duration_s=time.monotonic() - started,
        peak_memory_mb=peak_mb or None,
        timed_out=timed_out,
        out_of_memory=out_of_memory,
    )


def describe(backend: str) -> str:
    if backend == "bwrap":
        pid = "private PID namespace" if pid_namespace_supported() else "host /proc read-only"
        return f"bubblewrap: no network, read-only system and inputs, private /tmp, {pid}"
    if backend == "podman":
        runtime = "gVisor" if shutil.which("runsc") else "runc"
        return f"podman ({runtime}): no network, read-only rootfs"
    return "NO SANDBOX (trusted local test only)"
