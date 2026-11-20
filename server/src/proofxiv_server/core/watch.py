"""Automatic literature watch, without a model: keeps the catalog honest between weekly runs.

Sources, each read on its own schedule by the jobs loop (`run_due`):
- google-deepmind/formal-conjectures (daily): the current category of every imported statement and
  its `formal_proof` link; a statement tagged `research solved` upstream is a strong lead;
- teorth/erdosproblems `data/problems.yaml` (daily): Erdős problems marked proved, disproved or
  solved; strong for the main statement `erdos_N`, weak for its variants and parts;
- the Palomar registry of Lean proofs (daily): an entry that proves the statement's qualified name
  is a strong lead (and the link formal-conjectures asks for when a problem is marked solved);
- pull requests to formal-conjectures (hourly, as a queue): a PR that changes a statement's file is
  a weak lead, someone is working on it upstream;
- GitHub code search for the statement's name (only with PROOFXIV_GITHUB_TOKEN): repositories that
  mention it, a weak lead (this is how a prior solution of OEIS A060841 was found).

A lead is shown on the problem page and in the precheck; a strong one also becomes an unconfirmed
`solved` event for the weekly run to confirm (`auto_source` set: nobody earns points for it).
"""

from __future__ import annotations

import io
import re
import tarfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
import structlog
from proofxiv_protocol.enums import (
    DecisiveTaskKind,
    DecisiveTaskStatus,
    ExternalEventKind,
    ProblemStatus,
)
from proofxiv_protocol.leanscan import strip_comments
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import DecisiveTask, ExternalEvent, Problem, ProblemLead, WatchState
from ..db.session import sessionmaker
from .identity import get_or_create_operator, now
from .params import get_param

log = structlog.get_logger()

WATCH_LOGIN = "proofxiv-watch"
FC_REPO = "google-deepmind/formal-conjectures"
FC_TARBALL = f"https://codeload.github.com/{FC_REPO}/tar.gz/refs/heads/main"
ERDOS_YAML = "https://raw.githubusercontent.com/teorth/erdosproblems/main/data/problems.yaml"
PALOMAR_RESULTS = "https://data.palomar-registry.org/api/v1/results"
PALOMAR_ENTRY = "https://palomar-registry.org/entry.html?id={id}"
GITHUB_API = "https://api.github.com"

OPEN_STATES = (ProblemStatus.OPEN, ProblemStatus.PROPOSED)

# how the summary of an event found by the automatic watch (`auto_source`) starts, by its state:
# the weekly run's decision rewrites it, so the site and the digest never call a checked event unchecked
WATCH_PREFIX = {
    None: "Found by the automatic watch, not checked yet: ",
    True: "Found by the automatic watch, confirmed by the weekly run: ",
    False: "Found by the automatic watch, rejected by the weekly run: ",
}

ERDOS_SOLVED = {"proved", "disproved", "solved", "proved (Lean)", "disproved (Lean)", "solved (Lean)"}


# --- formal-conjectures: what upstream says about each statement -------------------------------


@dataclass
class UpstreamDecl:
    path: str
    name: str  # as written after `theorem`
    qualified: str  # with the enclosing namespaces
    category: str  # "research open", "research solved", "textbook", ...
    formal_proof: str | None


_SCOPE = re.compile(r"^[ \t]*(?:noncomputable[ \t]+)?(?:public[ \t]+)?(namespace|section|end)\b[ \t]*([^\s]*)", re.MULTILINE)
_DECL = re.compile(
    r"@\[(?P<attrs>[^\]]*)\]\s*(?:(?:private|protected|noncomputable|nonrec)\s+)*(?:theorem|lemma)\s+(?P<name>[^\s(:{\[]+)"
)
_CATEGORY = re.compile(r"\bcategory\s+(research\s+(?:open|solved)|[A-Za-z_]+)")
_FORMAL_PROOF = re.compile(r'formal_proof\s+using\s+\w+\s+at\s+"([^"]+)"')
_BLOB = re.compile(r"formal-conjectures/blob/[^/]+/(.+)$")


def parse_upstream(path: str, text: str) -> list[UpstreamDecl]:
    """Statements of one formal-conjectures file with their qualified names and categories."""
    code = strip_comments(text)
    marks: list[tuple[int, str, re.Match[str]]] = [(m.start(), "scope", m) for m in _SCOPE.finditer(code)]
    marks += [(m.start(), "decl", m) for m in _DECL.finditer(code)]
    stack: list[tuple[str, str]] = []
    out = []
    for _, kind, match in sorted(marks, key=lambda mark: mark[0]):
        if kind == "scope":
            word, name = match.group(1), match.group(2)
            if word == "end":
                if stack:
                    stack.pop()
            else:
                stack.append((word, name))
            continue
        attrs, name = match.group("attrs"), match.group("name")
        category = _CATEGORY.search(attrs)
        if category is None:
            continue
        namespaces = [n for word, n in stack if word == "namespace" and n]
        qualified = name[len("_root_.") :] if name.startswith("_root_.") else ".".join([*namespaces, name])
        proof = _FORMAL_PROOF.search(attrs)
        out.append(
            UpstreamDecl(path, name, qualified, " ".join(category.group(1).split()), proof.group(1) if proof else None)
        )
    return out


def parse_tarball(data: bytes) -> tuple[dict[tuple[str, str], UpstreamDecl], str]:
    """{(path, written name): statement} for the repository snapshot, and its commit."""
    decls: dict[tuple[str, str], UpstreamDecl] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        commit = str(tf.pax_headers.get("comment") or "main")
        for member in tf:
            parts = member.name.split("/", 1)
            if not member.isfile() or len(parts) < 2 or not parts[1].startswith("FormalConjectures/"):
                continue
            path = parts[1]
            if not path.endswith(".lean") or "/Util/" in path:
                continue
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            for decl in parse_upstream(path, extracted.read().decode("utf-8", errors="replace")):
                decls[(decl.path, decl.name)] = decl
    return decls, commit


def fc_key(problem: Problem) -> tuple[str, str] | None:
    """(path, written name) of an imported statement, from its source link and variant."""
    path = next((m.group(1) for s in problem.sources or [] if (m := _BLOB.search(str(s.get("url", ""))))), None)
    if path is None or not problem.variant:
        return None
    # two statements with one name in different files: the importer added the file in brackets
    return path, re.sub(r"\s+\(.*\)$", "", problem.variant)


# --- erdosproblems --------------------------------------------------------------------------------


def parse_erdos(text: str) -> dict[str, tuple[str, str]]:
    """{problem number: (status, last update)} from teorth/erdosproblems data/problems.yaml."""
    out = {}
    for block in re.split(r"\n(?=- number:)", text):
        number = re.search(r'number:\s*"?(\d+)"?', block)
        status = re.search(r'\n  status:\s*\n    state:\s*"([^"]+)"(?:\s*\n    last_update:\s*"([^"]*)")?', block)
        if number and status:
            out[number.group(1)] = (status.group(1), status.group(2) or "")
    return out


# --- leads and events -----------------------------------------------------------------------------


async def _watch_operator(session: AsyncSession) -> uuid.UUID:
    operator = await get_or_create_operator(session, WATCH_LOGIN, display_name="ProofXiv automatic watch")
    await session.flush()
    return operator.id


async def record_lead(
    session: AsyncSession, problem: Problem, source: str, url: str, title: str, detail: str, strong: bool
) -> ProblemLead:
    """Add a lead or refresh it; a new strong lead on an open problem becomes an unconfirmed
    `solved` event for the weekly run."""
    lead = (
        await session.execute(
            select(ProblemLead).where(ProblemLead.problem_id == problem.id, ProblemLead.source == source, ProblemLead.url == url)
        )
    ).scalar_one_or_none()
    if lead is None:
        lead = ProblemLead(problem_id=problem.id, source=source, url=url, last_seen_at=now())
        session.add(lead)
    lead.title, lead.detail, lead.strong, lead.last_seen_at = title[:1000], detail[:4000], strong, now()
    await session.flush()
    if strong and lead.event_id is None and problem.status in OPEN_STATES:
        event = ExternalEvent(
            kind=ExternalEventKind.SOLVED,
            problem_id=problem.id,
            source_url=url,
            source_title=title[:1000],
            summary=f"{WATCH_PREFIX[None]}{detail}"[:4000],
            reported_by=await _watch_operator(session),
            auto_source=source,
        )
        session.add(event)
        await session.flush()
        kinds = await get_param(session, "priority_kind")
        session.add(
            DecisiveTask(
                kind=DecisiveTaskKind.CONFIRM_EVENT,
                status=DecisiveTaskStatus.READY,
                base_priority=kinds.get("confirm_event", 60.0),
                event_id=event.id,
                problem_id=problem.id,
            )
        )
        lead.event_id = event.id
    return lead


async def _catalog(session: AsyncSession) -> list[Problem]:
    rows = await session.execute(select(Problem).where(Problem.origin == "formal-conjectures", Problem.hidden.is_(False)))
    return list(rows.scalars())


async def apply_formal_conjectures(
    session: AsyncSession, decls: dict[tuple[str, str], UpstreamDecl], commit: str
) -> dict[str, int]:
    stats = {"statements": 0, "leads": 0}
    for problem in await _catalog(session):
        key = fc_key(problem)
        if key is None:
            continue
        path, written = key
        blob = f"https://github.com/{FC_REPO}/blob/{commit}/{path}"
        upstream = {**(problem.upstream or {}), "repo": FC_REPO, "path": path, "commit": commit, "checked_at": now().isoformat()}
        decl = decls.get(key)
        stats["statements"] += 1
        if decl is None:
            upstream["missing"] = True
            if problem.status in OPEN_STATES:
                await record_lead(
                    session, problem, "formal-conjectures", blob, "No longer in formal-conjectures",
                    f"`{written}` is not in {path} at commit {commit[:12]}: renamed, moved or removed upstream.", False,
                )
                stats["leads"] += 1
        else:
            upstream.pop("missing", None)
            upstream.update(name=decl.qualified, category=decl.category, formal_proof=decl.formal_proof)
            if problem.status in OPEN_STATES and decl.category != "research open":
                solved = decl.category == "research solved"
                link = f" with a formal proof at {decl.formal_proof}" if decl.formal_proof else ""
                await record_lead(
                    session, problem, "formal-conjectures", decl.formal_proof or blob,
                    f"formal-conjectures tags it `{decl.category}`",
                    f"`{decl.qualified}` in {path} is tagged `category {decl.category}` at commit {commit[:12]}{link}.",
                    solved,
                )
                stats["leads"] += 1
        problem.upstream = upstream
    return stats


async def apply_erdos(session: AsyncSession, statuses: dict[str, tuple[str, str]]) -> dict[str, int]:
    stats = {"leads": 0}
    for problem in await _catalog(session):
        key = fc_key(problem)
        number = re.search(r"ErdosProblems/(\d+)\.lean$", key[0]) if key else None
        if key is None or number is None or problem.status not in OPEN_STATES:
            continue
        state, updated = statuses.get(number.group(1), ("", ""))
        if state not in ERDOS_SOLVED:
            continue
        main = key[1] == f"erdos_{number.group(1)}"
        note = "" if main else " This statement is a variant or a part: check whether it is settled too."
        await record_lead(
            session, problem, "erdosproblems", f"https://www.erdosproblems.com/{number.group(1)}",
            f"erdosproblems.com: {state}",
            f"erdosproblems.com lists Erdős problem #{number.group(1)} as {state}"
            + (f" (updated {updated})" if updated else "") + f".{note}",
            main,
        )
        stats["leads"] += 1
    return stats


async def apply_palomar(session: AsyncSession, entries: list[dict[str, Any]]) -> dict[str, int]:
    by_name: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if str(entry.get("status", "registered")) in ("withdrawn", "retracted"):
            continue
        for name in (entry.get("formalization") or {}).get("theorem_names") or []:
            by_name[str(name)] = entry
    stats = {"entries": len(entries), "leads": 0}
    for problem in await _catalog(session):
        name = (problem.upstream or {}).get("name")
        entry = by_name.get(name) if name else None
        if entry is None:
            continue  # settled problems keep the lead too: it is the formal_proof link upstream wants
        trust = (entry.get("trust") or {}).get("level", "?")
        await record_lead(
            session, problem, "palomar", PALOMAR_ENTRY.format(id=entry["id"]),
            f"Palomar {entry['id']}: {entry.get('title', '')}",
            f"A Lean proof of `{name}` is registered in Palomar (trust: {trust}, "
            f"{str(entry.get('published_at', ''))[:10]}).",
            True,
        )
        stats["leads"] += 1
    return stats


def _pr_touches(problems: list[Problem], filename: str, patch: str) -> list[Problem]:
    """Statements of a file a PR changes: those named in the diff, else all of them (settled ones
    too: a PR already open upstream is what `proofxiv owner upstream` must not duplicate)."""
    in_file = [p for p in problems if (k := fc_key(p)) and k[0] == filename]
    named = [p for p in in_file if (k := fc_key(p)) and k[1] in patch]
    return named or in_file


async def apply_pull(session: AsyncSession, pull: dict[str, Any], files: list[dict[str, Any]]) -> int:
    problems = await _catalog(session)
    state = "merged" if pull.get("merged_at") else str(pull.get("state", "open"))
    count = 0
    for file in files:
        patch = str(file.get("patch") or "")
        solves = re.search(r"^\+.*category\s+research\s+solved", patch, re.MULTILINE) is not None
        for problem in _pr_touches(problems, str(file.get("filename", "")), patch):
            await record_lead(
                session, problem, "formal-conjectures-pr", str(pull["html_url"]),
                f"PR #{pull['number']} ({state}): {pull.get('title', '')}",
                f"A pull request to formal-conjectures changes {file.get('filename')}"
                + (" and tags a statement `research solved`" if solves else "") + ".",
                False,
            )
            count += 1
    return count


_GENERIC = re.compile(r"\d{2,}")


def code_needle(problem: Problem) -> str | None:
    """What to search GitHub code for: the written name when it is distinctive (erdos_357...),
    else the qualified one (OeisA60841.conjecture1)."""
    key = fc_key(problem)
    qualified = (problem.upstream or {}).get("name")
    if key is None or not qualified:
        return None
    return key[1] if _GENERIC.search(key[1]) else qualified


async def apply_code_hits(session: AsyncSession, problem: Problem, needle: str, items: list[dict[str, Any]]) -> int:
    path = (problem.upstream or {}).get("path")
    count = 0
    for item in items:
        repo = str((item.get("repository") or {}).get("full_name", ""))
        lowered = repo.lower()
        if "formal-conjectures" in lowered or "formal_conjectures" in lowered or item.get("path") == path:
            continue  # the catalog itself, its forks and mirrors
        await record_lead(
            session, problem, "github", str(item.get("html_url")), f"{repo}: {item.get('path')}",
            f"A public repository mentions `{needle}`; it may hold a proof or a counterexample.", False,
        )
        count += 1
    return count


# --- fetching and scheduling ----------------------------------------------------------------------


def _github_headers() -> dict[str, str]:
    token = get_settings().github_token
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _state(session: AsyncSession, key: str) -> dict[str, Any]:
    row = await session.get(WatchState, key)
    return dict(row.value) if row else {}


async def _save_state(session: AsyncSession, key: str, value: dict[str, Any]) -> None:
    row = await session.get(WatchState, key)
    if row is None:
        session.add(WatchState(key=key, value=value))
    else:
        row.value = value


async def watch_formal_conjectures(client: httpx.AsyncClient, state: dict[str, Any]) -> dict[str, Any]:
    response = await client.get(FC_TARBALL)
    response.raise_for_status()
    decls, commit = parse_tarball(response.content)
    async with sessionmaker()() as session:
        stats = await apply_formal_conjectures(session, decls, commit)
        await session.commit()
    return {**stats, "commit": commit}


async def watch_erdos(client: httpx.AsyncClient, state: dict[str, Any]) -> dict[str, Any]:
    response = await client.get(ERDOS_YAML)
    response.raise_for_status()
    async with sessionmaker()() as session:
        stats = await apply_erdos(session, parse_erdos(response.text))
        await session.commit()
    return stats


async def watch_palomar(client: httpx.AsyncClient, state: dict[str, Any]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    params: dict[str, str] = {"order": "updated", "trust": "all"}
    for _ in range(200):  # 25 entries a page; the whole registry is about fifty pages
        response = await client.get(PALOMAR_RESULTS, params=params)
        response.raise_for_status()
        page = response.json()
        entries += page.get("entries") or []
        if not page.get("next"):
            break
        params["cursor"] = page["next"]
    async with sessionmaker()() as session:
        stats = await apply_palomar(session, entries)
        await session.commit()
    return stats


async def watch_pulls(client: httpx.AsyncClient, state: dict[str, Any]) -> dict[str, Any]:
    """New and updated PRs go into a queue; each run reads the files of a batch of them."""
    budget = 300 if get_settings().github_token else 40
    cursor = state.get("cursor") or (now() - timedelta(days=45)).isoformat()
    pending: dict[str, dict[str, Any]] = {str(p["number"]): p for p in state.get("pending", [])}
    newest = cursor
    for page in range(1, 6):
        response = await client.get(
            f"{GITHUB_API}/repos/{FC_REPO}/pulls",
            params={"state": "all", "sort": "updated", "direction": "desc", "per_page": "100", "page": str(page)},
            headers=_github_headers(),
        )
        response.raise_for_status()
        pulls = response.json()
        fresh = [p for p in pulls if str(p.get("updated_at", "")) > cursor]
        for pull in fresh:
            pending[str(pull["number"])] = {k: pull.get(k) for k in ("number", "title", "state", "merged_at", "html_url", "updated_at")}
            newest = max(newest, str(pull["updated_at"]))
        if len(fresh) < len(pulls) or not pulls:
            break
        budget -= 1
    done, leads = 0, 0
    for number in sorted(pending, key=int, reverse=True)[:budget]:  # newest first: they matter most
        response = await client.get(
            f"{GITHUB_API}/repos/{FC_REPO}/pulls/{number}/files", params={"per_page": "100"}, headers=_github_headers()
        )
        if response.status_code in (403, 429):
            break  # rate limited: the rest waits for the next run
        response.raise_for_status()
        async with sessionmaker()() as session:
            leads += await apply_pull(session, pending[number], response.json())
            await session.commit()
        del pending[number]
        done += 1
    return {"cursor": newest, "pending": list(pending.values()), "read": done, "leads": leads}


async def watch_code(client: httpx.AsyncClient, state: dict[str, Any]) -> dict[str, Any]:
    """GitHub code search needs a token; eight statements a run, the requested ones first."""
    if not get_settings().github_token:
        return {"skipped": "no PROOFXIV_GITHUB_TOKEN"}
    async with sessionmaker()() as session:
        problems = [p for p in await _catalog(session) if p.status in OPEN_STATES and code_needle(p)]
        problems.sort(key=lambda p: (not (p.upstream or {}).get("code_requested"), (p.upstream or {}).get("code_checked_at") or ""))
        ids = [p.id for p in problems[:8]]
    searched, leads = 0, 0
    for problem_id in ids:
        async with sessionmaker()() as session:
            problem = await session.get(Problem, problem_id)
            assert problem is not None
            needle = code_needle(problem)
            assert needle is not None
            response = await client.get(
                f"{GITHUB_API}/search/code", params={"q": f'"{needle}" language:Lean', "per_page": "30"}, headers=_github_headers()
            )
            if response.status_code in (403, 429):
                break
            response.raise_for_status()
            leads += await apply_code_hits(session, problem, needle, response.json().get("items") or [])
            upstream = {**(problem.upstream or {}), "code_checked_at": now().isoformat()}
            upstream.pop("code_requested", None)
            problem.upstream = upstream
            await session.commit()
            searched += 1
    return {"searched": searched, "leads": leads}


Watcher = Callable[[httpx.AsyncClient, dict[str, Any]], Awaitable[dict[str, Any]]]
SCHEDULE: dict[str, tuple[timedelta, Watcher]] = {
    "formal-conjectures": (timedelta(hours=24), watch_formal_conjectures),
    "erdosproblems": (timedelta(hours=24), watch_erdos),
    "palomar": (timedelta(hours=24), watch_palomar),
    "formal-conjectures-pr": (timedelta(hours=1), watch_pulls),
    "github": (timedelta(minutes=10), watch_code),
}
RETRY_AFTER = timedelta(hours=1)


def _due(state: dict[str, Any], every: timedelta, at: datetime) -> bool:
    last = state.get("failed_at") or state.get("ran_at")
    wait = RETRY_AFTER if state.get("failed_at") else every
    return last is None or datetime.fromisoformat(last) + wait <= at


async def run_due(only: set[str] | None = None, force: bool = False, reset: bool = False) -> dict[str, Any]:
    """Run the sources whose time has come (or `only` these, `force` ignoring the schedule;
    `reset` forgets their cursors first, e.g. to read 45 days of pull requests again)."""
    if not get_settings().watch_enabled and not force:
        return {}
    if reset:
        async with sessionmaker()() as session:
            for key in only or SCHEDULE:
                row = await session.get(WatchState, key)
                if row is not None:
                    await session.delete(row)
            await session.commit()
    results: dict[str, Any] = {}
    user_agent = f"ProofXiv literature watch (+{get_settings().public_url})"
    async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers={"User-Agent": user_agent}) as client:
        for key, (every, watcher) in SCHEDULE.items():
            if only is not None and key not in only:
                continue
            async with sessionmaker()() as session:
                state = await _state(session, key)
            if not force and not _due(state, every, now()):
                continue
            try:
                result = await watcher(client, state)
                state = {**state, **result, "ran_at": now().isoformat()}
                state.pop("failed_at", None)
                state.pop("error", None)
                log.info("watch", source=key, **{k: v for k, v in result.items() if k != "pending"})
            except Exception as exc:  # one source failing must not stop the others
                log.exception("watch failed", source=key)
                state = {**state, "failed_at": now().isoformat(), "error": str(exc)[:500]}
                result = {"error": str(exc)[:500]}
            async with sessionmaker()() as session:
                await _save_state(session, key, state)
                await session.commit()
            results[key] = result
    return results


async def request_code_search(session: AsyncSession, problem: Problem) -> bool:
    """Put a statement first in the code-search queue (precheck); False without a token."""
    if not get_settings().github_token or not code_needle(problem):
        return False
    checked = (problem.upstream or {}).get("code_checked_at")
    if checked and datetime.fromisoformat(checked) > now() - timedelta(days=1):
        return False
    problem.upstream = {**(problem.upstream or {}), "code_requested": True}
    return True
