"""Runner: lease machine-check jobs, execute them in the sandbox, sign and submit the results."""

from __future__ import annotations

import json
import secrets
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

from proofxiv_protocol.enums import RunKind, RunVerdict
from proofxiv_protocol.runs import CheckerReport, LeanReport, ReferenceReport, RunJobSpec, RunResult
from proofxiv_protocol.signing import SignedBundle, new_nonce, utc_now_iso

from . import __version__, lean, references, sandbox
from .client import Client
from .config import Config, workdir

Log = Callable[[str], None]
# how to run one checker implementation (see checkers.runboth.Execute)
CheckerExecute = Callable[[list[str], float | None], tuple[int | None, str, str, str | None]]


class RunnerSetupError(RuntimeError):
    """This runner cannot run the job (environment); the job goes back without a verdict."""


def capabilities(config: Config) -> dict:
    """What this runner can execute; the server only hands out matching jobs."""
    try:
        from . import checkers  # noqa: F401

        has_checkers = bool(config.rust_checker) and Path(config.rust_checker or "").exists()
    except ImportError:
        has_checkers = False
    return {
        "lean_versions": sorted(config.lean_toolchains),
        "mathlib_versions": sorted(config.lean_projects),
        "checkers": has_checkers,
        "references": config.check_references,
    }


def _fetch_inputs(client: Client, spec: RunJobSpec, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for ref in spec.inputs:
        client.download(ref.sha256, target / ref.path, base=target)


def _out_of_memory(result: sandbox.SandboxResult) -> bool:
    return result.out_of_memory or "std::bad_alloc" in result.stderr


def run_lean(config: Config, spec: RunJobSpec, inputs: Path) -> tuple[RunVerdict, LeanReport | None, str, str]:
    """Return (verdict, report, log text, runner description).

    Local modules the file imports are compiled first, dependencies first, each in the
    sandbox with the sources read-only; then the file itself is checked with the runner's
    fenced `#print axioms` commands appended."""
    target = spec.command[1] if len(spec.command) > 1 else next(
        r.path for r in spec.inputs if r.path.endswith(".lean")
    )
    sources = {
        p.relative_to(inputs).as_posix(): p.read_text(errors="replace") for p in inputs.rglob("*.lean")
    }
    modules, issues = lean.local_modules(sources, target)
    paths = [target] + [path for _, path in modules]
    for path in paths:  # the server's rules, applied again before running Lean
        found, _ = lean.scan_lean(sources[path])
        issues += [(f"{path}: " if path != target else "") + issue for issue in found]
    if issues:
        log = "refused before running Lean:\n" + "\n".join(issues)
        return RunVerdict.FAIL, LeanReport(errors=issues), log, f"proofxiv-worker {__version__}; static rules"
    try:
        env = lean.resolve_env(config, spec.image, any(lean.uses_mathlib(sources[p]) for p in paths))
    except (lean.LeanSetupError, OSError) as exc:
        raise RunnerSetupError(f"runner is not set up for this job: {exc}") from exc
    runner = f"proofxiv-worker {__version__}; {sandbox.describe(config.sandbox)}; {env.description}"

    work = inputs.parent / "work"
    if work.exists():
        shutil.rmtree(work)
    build = work / "build"
    build.mkdir(parents=True)
    lean_path = ":".join(p for p in (env.env.get("LEAN_PATH"), str(build)) if p)
    run_env = {**env.env, "LEAN_PATH": lean_path}
    root = (inputs / target).parent.resolve()
    deadline = time.monotonic() + spec.resources.minutes * 60
    log_parts: list[str] = []

    # Lean starts one thread per core of the machine unless told otherwise; memory is watched
    # by the sandbox (Lean's own -M does not stop compiled evaluation)
    limits = [f"-j{spec.resources.cores}"]

    def lean_run(args: list[str]) -> sandbox.SandboxResult:
        return sandbox.run(
            [str(env.lean), *limits, *args],
            workdir=work,
            ro_paths=[*env.ro_paths, inputs.resolve()],
            timeout_s=max(1, int(deadline - time.monotonic())),
            ram_gb=spec.resources.ram_gb,
            cores=spec.resources.cores,
            backend=config.sandbox,
            env=run_env,
        )

    for module, path in modules:
        olean = build / (module.replace(".", "/") + ".olean")
        olean.parent.mkdir(parents=True, exist_ok=True)
        result = lean_run(["-R", str(root), "-o", str(olean), str((inputs / path).resolve())])
        log_parts.append(f"$ lean -o {module}.olean {path}\n{result.stdout}\n{result.stderr}")
        if result.timed_out:
            return RunVerdict.ERROR, None, "\n".join(log_parts) + "\n[timeout]", runner
        if _out_of_memory(result):
            return RunVerdict.INSUFFICIENT_RESOURCES, None, "\n".join(log_parts), runner
        errors = [line.strip() for line in (result.stdout + result.stderr).splitlines() if ": error" in line]
        if result.returncode != 0 or errors:
            report = LeanReport(errors=[f"module {module} does not compile"] + errors[:49])
            return RunVerdict.FAIL, report, "\n".join(log_parts), runner

    check_file = work / "ProofXivCheck.lean"
    nonce = secrets.token_hex(16)
    check_file.write_text(lean.check_source(sources[target], spec.lean_theorems, nonce))
    result = lean_run([str(check_file)])
    log_parts.append(f"$ lean ProofXivCheck.lean  ({env.description})\n{result.stdout}\n{result.stderr}")
    log = "\n".join(log_parts)
    if result.timed_out:
        return RunVerdict.ERROR, None, log + "\n[timeout]", runner
    if _out_of_memory(result):
        return RunVerdict.INSUFFICIENT_RESOURCES, None, log, runner
    report = lean.parse_output(result.stdout + "\n" + result.stderr, nonce)
    if result.returncode != 0 and not report.errors:
        report.errors.append(f"lean exited with code {result.returncode}")
    missing = [t for t in spec.lean_theorems if t not in report.theorems]
    if report.errors or report.has_sorry or missing:
        if missing:
            report.errors.append(f"axioms not reported for: {missing}")
        return RunVerdict.FAIL, report, log, runner
    return RunVerdict.PASS, report, log, runner


def run_checker(config: Config, spec: RunJobSpec, inputs: Path) -> tuple[RunVerdict, CheckerReport | None, str, str]:
    try:
        from .checkers import run_both
    except ImportError as exc:
        raise RunnerSetupError("reference checkers are not installed in this worker") from exc
    if not spec.certificate_type:
        return RunVerdict.ERROR, None, "job has no certificate type", ""
    path = inputs / spec.inputs[0].path
    outcome = run_both(
        spec.certificate_type,
        path.resolve(),
        config.rust_checker,
        timeout=spec.resources.minutes * 60,
        execute=_sandboxed_checker(config, spec, inputs),
    )
    report = CheckerReport(
        checker=spec.certificate_type,
        implementations=outcome.get("implementations", {}),
        details=str(outcome.get("details", ""))[:4000],
    )
    log = json.dumps(outcome, ensure_ascii=False, indent=2, default=str)
    runner = (
        f"proofxiv-worker {__version__}; reference checkers (python + rust); "
        f"{sandbox.describe(config.sandbox)}"
    )
    # both implementations accept the certificate and agree on what they computed
    ok = bool(outcome.get("ok")) and len(report.implementations) >= 2
    return (RunVerdict.PASS if ok else RunVerdict.FAIL), report, log, runner


def _sandboxed_checker(config: Config, spec: RunJobSpec, inputs: Path) -> CheckerExecute:
    """Run each checker implementation in the sandbox: the certificate and the checkers'
    code are read-only, memory and time are limited, there is no network."""
    import proofxiv_worker

    work = inputs.parent / "check-work"
    work.mkdir(exist_ok=True)
    code_roots = [
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(proofxiv_worker.__file__).resolve().parents[1],  # the package (editable install)
        inputs.resolve(),
    ]

    def run(cmd: list[str], timeout: float | None) -> tuple[int | None, str, str, str | None]:
        result = sandbox.run(
            cmd,
            workdir=work,
            ro_paths=[*code_roots, Path(cmd[0]).resolve().parent],
            timeout_s=timeout or spec.resources.minutes * 60,
            ram_gb=spec.resources.ram_gb,
            cores=spec.resources.cores,
            backend=config.sandbox,
        )
        if result.timed_out:
            return None, result.stdout, result.stderr, f"timed out after {timeout} s"
        return result.returncode, result.stdout, result.stderr, None

    return run


def run_references(
    config: Config, spec: RunJobSpec, inputs: Path, fetch: references.Fetch | None = None
) -> tuple[RunVerdict, ReferenceReport, str, str]:
    """Look the bibliography up in arXiv and Crossref. The files are only parsed as text,
    nothing in them runs, so this needs no sandbox; it needs the network."""
    files = {ref.path: (inputs / ref.path).read_text(errors="replace") for ref in spec.inputs}
    resolver = references.Resolver(
        fetch or references.http_fetch(config.contact_email),
        config.contact_email,
        deadline=time.monotonic() + max(60, spec.resources.minutes * 60 - 60),
    )
    try:
        report = references.check(files, resolver)
    except references.Unavailable as exc:
        raise RunnerSetupError(f"no bibliography service answered: {exc}") from exc
    log = "\n".join(
        f"{item.status:<10} {item.key}: {item.note or item.found_title or ''}".rstrip() for item in report.items
    )
    runner = f"proofxiv-worker {__version__}; references via the arXiv API, Crossref and DataCite"
    return RunVerdict.PASS, report, log or "no references found", runner


def execute(client: Client, config: Config, spec: RunJobSpec) -> RunResult:
    job_dir = workdir("runs", str(spec.job_id))
    inputs = job_dir / "in"
    _fetch_inputs(client, spec, inputs)
    lean_report = checker_report = references_report = None
    if spec.kind is RunKind.LEAN:
        verdict, lean_report, log, runner = run_lean(config, spec, inputs)
    elif spec.kind is RunKind.REFERENCES:
        verdict, references_report, log, runner = run_references(config, spec, inputs)
    else:
        verdict, checker_report, log, runner = run_checker(config, spec, inputs)
    log_sha = client.upload(log.encode(), f"run-{spec.job_id}.log")
    return RunResult(
        job_id=spec.job_id,
        job_sha256=spec.digest(),
        verdict=verdict,
        lean=lean_report,
        checker=checker_report,
        references=references_report,
        log_sha256=log_sha,
        runner=runner or f"proofxiv-worker {__version__}",
        image_digest=spec.image,
        issued_at=utc_now_iso(),
        nonce=new_nonce(),
    )


def loop(
    client: Client,
    config: Config,
    private_key: str,
    log: Log = print,
    max_jobs: int | None = None,
) -> int:
    done = 0
    released: list[str] = []
    caps = capabilities(config)
    while max_jobs is None or done < max_jobs:
        raw = client.post(
            "/runs/lease",
            json={
                "ram_gb": config.runner_ram_gb,
                "cores": config.runner_cores,
                "minutes": config.runner_minutes,
                **caps,
                "exclude_jobs": released,
            },
        )
        if raw is None:
            break
        spec = RunJobSpec.model_validate(raw)
        log(f"run job {spec.job_id}: {spec.kind.value} {spec.command}")
        try:
            result = execute(client, config, spec)
        except RunnerSetupError as exc:
            client.post(f"/runs/{spec.job_id}/release", json={"reason": str(exc)})
            released.append(str(spec.job_id))
            log(f"  given back (no verdict): {exc}")
            continue
        bundle = SignedBundle.create(result, private_key)
        out = client.post("/runs/results", json=bundle.model_dump(mode="json"))
        log(f"  verdict={result.verdict.value} accepted={out['accepted']} ({out['evaluation']})")
        done += 1
    return done
