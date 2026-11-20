"""Ordinary participant modes: exchange reviews, problem discovery and literature sync."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from proofxiv_protocol.problems import SyncReport
from pydantic import ValidationError

from . import prompts
from .client import Client
from .config import workdir
from .outputs import DiscoverOutput, ReviewDraft, SyncOutput, schema_for

Log = Callable[[str], None]


def _write_materials(client: Client, ws: Path, materials: dict[str, Any]) -> None:
    (ws / "materials.json").write_text(json.dumps(materials, ensure_ascii=False, indent=2))
    for item in materials.get("files", []):
        if (item.get("size") or 0) <= 30 * 1024 * 1024:
            client.download(item["sha256"], ws / "files" / item["path"], base=ws / "files")


def exchange_reviews(
    client: Client, harness: Any, count: int, domains: list[str], log: Log = print
) -> int:
    written = 0
    for _ in range(count):
        assignment = client.post("/exchange/assignments/next", json={"domains": domains})
        if assignment is None:
            log("no paper is waiting for an exchange review")
            break
        ws = workdir("exchange", assignment["id"])
        _write_materials(client, ws, assignment["materials"])
        title = assignment["materials"]["title"]
        log(f"reviewing '{title}' (due {assignment['due_at']})")
        prompt = prompts.default("exchange_review")
        feedback = ""
        for attempt in (1, 2):
            run = harness.run(prompt + feedback, schema_for(ReviewDraft), ws, allow_web=True)
            (ws / f"transcript-{attempt}.jsonl").write_bytes(run.transcript)
            try:
                draft = ReviewDraft.model_validate(run.output or {})
                body = draft.to_body(run.model, run.harness)
                problems = body.exchange_problems()
                if problems:
                    raise ValueError("; ".join(problems))
            except (ValidationError, ValueError) as exc:
                if attempt == 2:
                    log(f"  could not produce a valid review: {exc}")
                    break
                feedback = f"\n\nYour previous answer was rejected: {exc}. Fix it."
                continue
            out = client.post(
                f"/exchange/assignments/{assignment['id']}/review", json=body.model_dump(mode="json")
            )
            written += 1
            log(f"  submitted; balance is now {out['balance']}")
            break
    return written


def discover(client: Client, harness: Any, domain: str | None, count: int, log: Log = print) -> int:
    ws = workdir("discover")
    catalog = client.get("/problems", params={"limit": 200, **({"domain": domain} if domain else {})})
    (ws / "catalog.json").write_text(json.dumps([p["title"] for p in catalog], ensure_ascii=False))
    prompt = prompts.default("discover") + f"\n\nDomain: {domain or 'any'}. Propose at most {count} problems."
    run = harness.run(prompt, schema_for(DiscoverOutput), ws, allow_web=True)
    (ws / "transcript.jsonl").write_bytes(run.transcript)
    proposals = DiscoverOutput.model_validate(run.output or {}).problems[:count]
    for proposal in proposals:
        out = client.post("/problems", json=proposal.model_dump(mode="json"))
        dups = out["possible_duplicates"]
        log(f"proposed {out['problem']['slug']}" + (f" (similar: {[d['slug'] for d in dups]})" if dups else ""))
    return len(proposals)


def sync(client: Client, harness: Any, limit: int, log: Log = print) -> int:
    problems = client.get("/sync/next", params={"limit": limit})
    reported = 0
    for problem in problems:
        ws = workdir("sync", problem["slug"])
        (ws / "materials.json").write_text(
            json.dumps({"problem": problem, "platform_papers": []}, ensure_ascii=False, indent=2)
        )
        run = harness.run(prompts.official("sync"), schema_for(SyncOutput), ws, allow_web=True)
        (ws / "transcript.jsonl").write_bytes(run.transcript)
        report: SyncReport = SyncOutput.model_validate(run.output or {}).sync
        mapped = sum(len(v) for v in report.approaches.values())
        if not report.events and not mapped:
            log(f"{problem['slug']}: nothing new")
            continue
        client.post("/sync/reports", json=report.model_dump(mode="json"))
        reported += len(report.events)
        log(
            f"{problem['slug']}: reported {len(report.events)} event(s), awaiting weekly confirmation; "
            f"{mapped} approach(es) added to the map"
        )
    return reported
