"""The weekly trusted run: runner jobs first, then decisive tasks, each signed and published."""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from proofxiv_protocol.enums import DecisiveTaskKind
from proofxiv_protocol.signing import SignedBundle, new_nonce, utc_now_iso
from proofxiv_protocol.trusted import DecisiveTaskSpec, ModerationDecision, TrustedResult
from pydantic import ValidationError

from . import __version__, config, prompts, runner
from .client import ApiError, Client
from .config import Config, workdir
from .harness import HarnessResult
from .outputs import OUTPUT_FOR_KIND, WEB_KINDS, ReviewOutput, schema_for

Log = Callable[[str], None]
MAX_FILE_BYTES = 30 * 1024 * 1024


@dataclass
class WeeklyStats:
    runs: int = 0
    tasks_done: int = 0
    tasks_failed: int = 0
    cost_usd: float = 0.0
    by_kind: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


def _paper_files(materials: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("paper", "target"):
        block = materials.get(key)
        if isinstance(block, dict):
            if "files" in block:
                return block["files"]
            if isinstance(block.get("paper"), dict):
                return block["paper"].get("files", [])
    return []


def secrets_in(data: bytes, cfg: Config) -> list[str]:
    """Which of this machine's secrets appear in what is about to be published (a review and its
    transcript are public): an answer that carries one is never sent."""
    found = []
    for label, path in (("the signing key", cfg.key_file), ("the owner key", cfg.owner_key_file)):
        if path and Path(path).exists():
            secret = Path(path).read_text().strip().encode()
            if secret and secret in data:
                found.append(label)
    if cfg.token and cfg.token.encode() in data:
        found.append("the API token")
    if re.search(rb"pxv_[A-Za-z0-9_-]{20,}", data):
        found.append("something that looks like a ProofXiv token")
    if re.search(rb"sk-ant-[A-Za-z0-9_-]{10,}", data):
        found.append("something that looks like an Anthropic API key")
    return found


def prepare_workspace(client: Client, run_id: str, spec: DecisiveTaskSpec) -> Path:
    ws = workdir("weekly", run_id, str(spec.task_id))
    (ws / "materials.json").write_text(json.dumps(spec.materials, ensure_ascii=False, indent=2))
    for item in _paper_files(spec.materials):
        if (item.get("size") or 0) <= MAX_FILE_BYTES:
            client.download(item["sha256"], ws / "files" / item["path"], base=ws / "files")
    return ws


def build_prompt(text: str, spec: DecisiveTaskSpec, ws: Path) -> str:
    files = sorted(str(p.relative_to(ws)) for p in (ws / "files").rglob("*") if p.is_file()) if (ws / "files").exists() else []
    lines = [
        text.rstrip(),
        "",
        "## This task",
        f"- kind: {spec.kind.value}{' (adversarial)' if spec.adversarial else ''}",
        f"- task id: {spec.task_id}",
        f"- materials: ./materials.json (sha256 {spec.materials_sha256})",
    ]
    if spec.avoid_vendors:
        lines.append(f"- vendors already used for this paper's anchor reviews: {', '.join(spec.avoid_vendors)}")
    if files:
        lines.append("- files: " + ", ".join(files[:200]))
    lines.append("")
    lines.append("Work through the task, then return the JSON object.")
    return "\n".join(lines)


def pick_harness(harnesses: dict[str, Any], avoid: list[str]) -> Any:
    for vendor, harness in harnesses.items():
        if vendor not in avoid:
            return harness
    return next(iter(harnesses.values()))  # single-vendor fallback, labelled by the server


def to_result(spec: DecisiveTaskSpec, run: HarnessResult, transcript_sha: str) -> TrustedResult:
    kind = spec.kind.value
    model_cls = OUTPUT_FOR_KIND[kind]
    parsed = model_cls.model_validate(run.output or {})
    fields: dict[str, Any] = {}
    if isinstance(parsed, ReviewOutput):
        fields["review"] = parsed.review.to_body(run.model, run.harness)
        fields["resolves_problem_slugs"] = parsed.resolves_problem_slugs
        if spec.kind in (DecisiveTaskKind.NOVELTY_CONSISTENCY, DecisiveTaskKind.NOVELTY_AUDIT):
            fields["novelty_category"] = parsed.novelty_category or parsed.review.novelty.category
    elif kind == "moderation":
        assert spec.target.report_id is not None
        fields["moderation"] = ModerationDecision(report_id=spec.target.report_id, **parsed.model_dump())
    elif kind in ("confirm_problem", "confirm_event"):
        fields["confirm"] = parsed.confirm  # type: ignore[attr-defined]
        fields["novelty_category"] = parsed.novelty_category  # type: ignore[attr-defined]
    elif kind == "sync":
        fields["sync"] = parsed.sync  # type: ignore[attr-defined]
    elif kind == "digest":
        fields["digest_md"] = parsed.digest_md  # type: ignore[attr-defined]
    return TrustedResult(
        task_id=spec.task_id,
        kind=spec.kind,
        materials_sha256=spec.materials_sha256,
        prompt_sha256=spec.prompt_sha256,
        vendor=run.vendor,
        model=run.model,
        harness=run.harness,
        request_ids=run.request_ids,
        transcript_sha256=transcript_sha,
        client_version=__version__,
        issued_at=utc_now_iso(),
        nonce=new_nonce(),
        **fields,
    )


def process_task(
    client: Client,
    private_key: str,
    harnesses: dict[str, Any],
    run_id: str,
    spec: DecisiveTaskSpec,
    log: Log,
    stats: WeeklyStats,
) -> None:
    prompt_doc = client.get(f"/prompts/{spec.prompt_name}/{spec.prompt_version}")
    text = prompt_doc["text"]
    held_out = text is None  # the server has only its hash; the text is kept on this machine
    if held_out:
        text = prompts.heldout_text(spec.prompt_name, spec.prompt_version)
        if text is None:
            path = prompts.heldout_path(spec.prompt_name, spec.prompt_version)
            raise RuntimeError(f"held-out prompt {spec.prompt_name} v{spec.prompt_version} is not at {path}")
    if prompts.sha256(text) != spec.prompt_sha256:
        raise RuntimeError("official prompt text does not match its hash")
    local_name = spec.prompt_name
    if local_name in prompts.OFFICIAL_NAMES and prompts.official(local_name) != text:
        log(f"  note: server prompt '{local_name}' v{spec.prompt_version} differs from the local copy")
    ws = prepare_workspace(client, run_id, spec)
    harness = pick_harness(harnesses, spec.avoid_vendors)
    schema = schema_for(OUTPUT_FOR_KIND[spec.kind.value])
    prompt = build_prompt(text, spec, ws)
    feedback = ""
    for attempt in (1, 2):
        run = harness.run(prompt + feedback, schema, ws, allow_web=spec.kind.value in WEB_KINDS)
        stats.cost_usd += run.cost_usd or 0.0
        (ws / f"transcript-{attempt}.jsonl").write_bytes(run.transcript)
        if run.error or run.output is None:
            raise RuntimeError(run.error or "the model returned no JSON")
        # a held-out prompt is in its transcript: private until the owner reveals the prompt
        leaked = secrets_in(run.transcript + json.dumps(run.output).encode(), config.load())
        if leaked:
            raise RuntimeError(f"not sent: the answer or its transcript contains {', '.join(leaked)}")
        transcript_sha = client.upload(
            run.transcript, f"transcript-{spec.task_id}-{attempt}.jsonl", embargo=held_out
        )
        try:
            result = to_result(spec, run, transcript_sha)
            bundle = SignedBundle.create(result, private_key)
            client.post("/trusted/results", json=bundle.model_dump(mode="json"))
            stats.tasks_done += 1
            stats.by_kind[spec.kind.value] = stats.by_kind.get(spec.kind.value, 0) + 1
            log(f"  done ({run.vendor}/{run.model}, ${run.cost_usd or 0:.2f})")
            return
        except (ValidationError, ApiError) as exc:
            if attempt == 2:
                raise
            message = exc.payload if isinstance(exc, ApiError) else str(exc)
            log(f"  rejected, retrying once: {str(message)[:300]}")
            feedback = (
                "\n\n## Your previous answer was rejected\n"
                f"{json.dumps(message, default=str)[:4000]}\nFix exactly this and answer again."
            )


def weekly(
    client: Client,
    config: Config,
    private_key: str,
    harnesses: dict[str, Any],
    budget_usd: float | None = None,
    max_tasks: int = 100,
    with_runner: bool = True,
    log: Log = print,
) -> WeeklyStats:
    stats = WeeklyStats()
    if with_runner:
        log("== machine checks")
        stats.runs = runner.loop(client, config, private_key, log)
    run = client.post("/trusted/weekly-runs", json={"budget": {"usd": budget_usd, "max_tasks": max_tasks}})
    run_id = run["id"]
    log(f"== weekly run {run_id}")
    try:
        while stats.tasks_done + stats.tasks_failed < max_tasks:
            if budget_usd is not None and stats.cost_usd >= budget_usd:
                log(f"budget reached (${stats.cost_usd:.2f})")
                break
            specs = client.post(f"/trusted/weekly-runs/{run_id}/tasks", json={"limit": 3})
            if not specs:
                break
            for raw in specs:
                spec = DecisiveTaskSpec.model_validate(raw)
                log(f"task {spec.kind.value} {spec.task_id} (priority {spec.priority:.1f})")
                try:
                    process_task(client, private_key, harnesses, run_id, spec, log, stats)
                except Exception as exc:  # one bad task must not stop the run
                    stats.tasks_failed += 1
                    stats.failures.append(f"{spec.kind.value} {spec.task_id}: {exc}")
                    log(f"  FAILED: {exc}")
                    # back to the queue at once instead of waiting for the lease to expire
                    with contextlib.suppress(ApiError):
                        client.post(
                            f"/trusted/tasks/{spec.task_id}/release",
                            json={"reason": str(exc)[:1900] or "failed"},
                        )
    finally:
        client.post(
            f"/trusted/weekly-runs/{run_id}/finish",
            json={"stats": {**stats.__dict__, "failures": stats.failures[:50]}},
        )
    log(f"== finished: {stats.tasks_done} done, {stats.tasks_failed} failed, ${stats.cost_usd:.2f}")
    return stats
