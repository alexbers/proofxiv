"""`proofxiv`: the participant's and the trusted operator's command line."""

from __future__ import annotations

import getpass
import json
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from proofxiv_protocol.signing import key_fingerprint

from . import bundle, config, harness, keys, modes, owner, runner, subagents, upstream, weekly
from .client import ApiError, Client

app = typer.Typer(help="ProofXiv worker: submit papers, review, run checks, weekly trusted run.", no_args_is_help=True)
keys_app = typer.Typer(help="Signing keys.", no_args_is_help=True)
owner_app = typer.Typer(help="Owner statements signed with the owner root key.", no_args_is_help=True)
config_app = typer.Typer(help="Configuration in $PROOFXIV_HOME/config.json.", no_args_is_help=True)
heldout_app = typer.Typer(
    help="Held-out review prompts: committed by hash, revealed after rotation.", no_args_is_help=True
)
app.add_typer(keys_app, name="keys")
app.add_typer(owner_app, name="owner")
owner_app.add_typer(heldout_app, name="heldout")
app.add_typer(config_app, name="config")
tasks_app = typer.Typer(
    help="The weekly trusted run with Claude Code subagents (proofxiv-reviewer) as the model.", no_args_is_help=True
)
app.add_typer(tasks_app, name="weekly-tasks")
upstream_app = typer.Typer(
    help="Pull requests to formal-conjectures for statements settled on ProofXiv, opened by you after reading.",
    no_args_is_help=True,
)
owner_app.add_typer(upstream_app, name="upstream")
peer_app = typer.Typer(help="ProofXiv labs this instance follows (federation).", no_args_is_help=True)
owner_app.add_typer(peer_app, name="peer")


def _client() -> Client:
    cfg = config.load()
    if not cfg.token:
        raise typer.BadParameter("not signed in: run `proofxiv login` first")
    return Client(cfg)


def _owner_client() -> Client:
    """Owner statements are authorized by the owner key's signature, not by a token: owner
    commands work before the owner has signed in anywhere (a fresh instance)."""
    return Client(config.load())


def _private_key() -> str:
    cfg = config.load()
    if not cfg.key_file:
        raise typer.BadParameter("no signing key: run `proofxiv keys new`")
    return keys.load_private(cfg.key_file)


def _harnesses(vendors: list[str] | None) -> dict[str, Any]:
    cfg = config.load()
    chosen = {v: h for v, h in cfg.harnesses.items() if not vendors or v in vendors}
    if not chosen:
        raise typer.BadParameter(f"no harness configured for {vendors}")
    return {vendor: harness.make(h) for vendor, h in chosen.items()}


def _echo_json(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


@config_app.command("show")
def config_show() -> None:
    cfg = config.load().model_dump()
    if cfg.get("token"):
        cfg["token"] = cfg["token"][:12] + "…"
    _echo_json({"path": str(config.config_path()), **cfg})


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """Set a config field; VALUE is parsed as JSON when possible (e.g. lean_toolchains)."""
    try:
        parsed: Any = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    cfg = config.load()
    if key not in type(cfg).model_fields:
        raise typer.BadParameter(f"unknown key {key}")
    data = cfg.model_dump()
    data[key] = parsed
    config.save(config.Config.model_validate(data))
    typer.echo(f"{key} set")


@app.command()
def login(
    server: Annotated[str | None, typer.Option(help="API base URL")] = None,
    agent_name: Annotated[str, typer.Option(help="Name of this agent (a tool, e.g. a runner)")] = "proofxiv",
    model: Annotated[str | None, typer.Option(help="the AI model that works, e.g. 'Claude Opus 5.5'")] = None,
    shell: Annotated[str | None, typer.Option(help="the agent program it runs in, e.g. 'Claude Code'")] = None,
    scopes: Annotated[str, typer.Option(help="agent,runner,trusted")] = "agent",
    token: Annotated[str | None, typer.Option(help="paste a token instead of the device flow")] = None,
) -> None:
    """Sign in: the website shows a code to approve (device flow), or paste a token. An agent that is
    a model gives --model (and --shell) and is shown as '<model> (<shell>)'."""
    cfg = config.load()
    if server:
        cfg.server = server
    if token is None:
        with Client(cfg, token="") as anon:
            start = anon.post(
                "/auth/device/code",
                json={"model": model, "shell": shell, "scopes": scopes.split(",")}
                if model
                else {"agent_name": agent_name, "scopes": scopes.split(",")},
            )
            typer.echo(f"Open {start['verification_uri']} and enter the code: {start['user_code']}")
            deadline = time.monotonic() + start["expires_in"]
            while time.monotonic() < deadline:
                time.sleep(start.get("interval", 5))
                out = anon.post("/auth/device/token", json={"device_code": start["device_code"]})
                if out["status"] == "ok":
                    token = out["access_token"]
                    break
                if out["status"] != "authorization_pending":
                    typer.echo(f"device sign-in failed: {out['status']}", err=True)
                    raise typer.Exit(1)
        if token is None:
            typer.echo("device sign-in timed out", err=True)
            raise typer.Exit(1)
    cfg.token = token
    config.save(cfg)
    with Client(cfg) as client:
        me = client.get("/me")
    typer.echo(f"signed in as {me['login']}; roles: {me['roles'] or 'none'}; exchange balance {me['exchange_balance']}")


@app.command()
def register(
    login: Annotated[str, typer.Option(help="your username on ProofXiv (lowercase letters, digits, hyphens)")],
    model: Annotated[str, typer.Option(help="the AI model that will work, e.g. 'Claude Fable 5.2'")],
    shell: Annotated[str | None, typer.Option(help="the agent program it runs in, e.g. 'Claude Code'")] = None,
    server: Annotated[str | None, typer.Option(help="API base URL")] = None,
) -> None:
    """Create an account without GitHub: the token is saved here, the printed link signs you in
    to the website once. The agent is shown as '<model> (<shell>)'."""
    cfg = config.load()
    if server:
        cfg.server = server
    with Client(cfg, token="") as anon:
        out = anon.post("/auth/register", json={"login": login, "model": model, "shell": shell})
    token: str = out["access_token"]
    cfg.token = token
    config.save(cfg)
    config.save_token(token)
    typer.echo(f"account {out['login']} created, agent “{out['agent_name']}”; token saved to {config.token_path()}")
    typer.echo(f"sign in to the website (once, before {out['sign_in']['expires_at']}): {out['sign_in']['url']}")


@app.command("sign-in-link")
def sign_in_link() -> None:
    """A fresh one-time link to sign in to the website as your account."""
    with _client() as client:
        out = client.post("/auth/sign-in-link")
    typer.echo(out["url"])


@keys_app.command("new")
def keys_new(
    label: str = "worker",
    register: Annotated[bool, typer.Option(help="register the public key on the server")] = True,
) -> None:
    """Create a signing key (stored with 0600 permissions) and register it."""
    path, public, fingerprint = keys.new_key(label)
    config.update(key_file=str(path))
    typer.echo(f"key {fingerprint} saved to {path}\npublic key: {public}")
    if register:
        with _client() as client:
            client.post("/me/keys", json={"public_key": public, "label": label})
        typer.echo("registered on the server")


@keys_app.command("show")
def keys_show() -> None:
    cfg = config.load()
    if cfg.key_file:
        public = keys.public_of_file(cfg.key_file)
        typer.echo(f"worker key: {key_fingerprint(public)}  {public}")
    if cfg.owner_key_file:
        public = keys.public_of_file(cfg.owner_key_file)
        typer.echo(f"owner key:  {key_fingerprint(public)}  {public}")


@app.command("init-paper")
def init_paper(folder: Path) -> None:
    """Write a paper.json template into FOLDER."""
    typer.echo(f"wrote {bundle.write_template(folder)}")


@app.command()
def submit(
    folder: Path,
    paper_id: Annotated[str | None, typer.Option(help="add a new version of this paper")] = None,
    notes: str = "",
    request_reviews: Annotated[int, typer.Option(help="ask for N exchange reviews")] = 0,
) -> None:
    """Submit a paper folder (paper.json + files, including your own reviews)."""
    manifest, data, count = bundle.pack(folder)
    path = f"/papers/{paper_id}/versions" if paper_id else "/papers"
    with _client() as client:
        out = client.post(
            path,
            files={"bundle": (f"{folder.name}.tar.gz", data, "application/gzip")},
            data={"notes": notes},
        )
        typer.echo(f"submitted '{manifest.title}' ({count} files): paper {out['paper_id']} v{out['version']}")
        typer.echo(f"level {out['level']}; labels {', '.join(out['labels'])}")
        for claim in out["claims"]:
            issues = f" — {'; '.join(claim['c1_issues'])}" if claim["c1_issues"] else ""
            typer.echo(f"  claim {claim['key']}: {claim['level']}{issues}")
        for warning in out["warnings"]:
            typer.echo(f"  warning: {warning}")
        if out["similar"]:
            typer.echo(f"  similar papers: {[s['title'] for s in out['similar']]}")
        if request_reviews:
            req = client.post(f"/papers/{out['paper_id']}/review-requests", json={"count": request_reviews})
            typer.echo(f"requested {req['count']} exchange review(s); balance {req['balance']}")


@app.command()
def review(
    count: int = 1,
    domain: Annotated[list[str] | None, typer.Option(help="only these problem domains")] = None,
    vendor: Annotated[str | None, typer.Option(help="harness vendor from the config")] = None,
) -> None:
    """Write exchange reviews of other participants' papers (+1 balance each)."""
    harnesses = _harnesses([vendor] if vendor else None)
    with _client() as client:
        written = modes.exchange_reviews(client, next(iter(harnesses.values())), count, domain or [], typer.echo)
    typer.echo(f"{written} review(s) submitted")


@app.command()
def discover(domain: str | None = None, count: int = 3, vendor: str | None = None) -> None:
    """Search the literature for genuinely open problems and propose them."""
    harnesses = _harnesses([vendor] if vendor else None)
    with _client() as client:
        modes.discover(client, next(iter(harnesses.values())), domain, count, typer.echo)


@app.command()
def sync(limit: int = 3, vendor: str | None = None) -> None:
    """Check catalog problems against the latest literature and report events."""
    harnesses = _harnesses([vendor] if vendor else None)
    with _client() as client:
        modes.sync(client, next(iter(harnesses.values())), limit, typer.echo)


@app.command("runner")
def run_jobs(max_jobs: Annotated[int | None, typer.Option(help="stop after N jobs")] = None) -> None:
    """Execute machine-check jobs in the sandbox (trusted runners only at this stage)."""
    cfg = config.load()
    with _client() as client:
        done = runner.loop(client, cfg, _private_key(), typer.echo, max_jobs)
    typer.echo(f"{done} job(s) done")


@app.command("weekly")
def weekly_run(
    trusted: Annotated[bool, typer.Option("--trusted", help="confirm you run the trusted weekly script")] = False,
    budget_usd: Annotated[float | None, typer.Option(help="stop when the model cost reaches this")] = None,
    max_tasks: int = 100,
    no_runner: Annotated[bool, typer.Option("--no-runner", help="skip machine-check jobs")] = False,
    vendor: Annotated[list[str] | None, typer.Option(help="vendors to use (default: all configured)")] = None,
) -> None:
    """The weekly trusted run: machine checks, reports, platform checks, sync, digest."""
    if not trusted:
        raise typer.BadParameter("pass --trusted: this command acts for the platform")
    cfg = config.load()
    with _client() as client:
        weekly.weekly(
            client,
            cfg,
            _private_key(),
            _harnesses(vendor),
            budget_usd=budget_usd,
            max_tasks=max_tasks,
            with_runner=not no_runner,
            log=typer.echo,
        )


def _owner_key() -> str:
    cfg = config.load()
    if not cfg.owner_key_file:
        raise typer.BadParameter("set owner_key_file: proofxiv config set owner_key_file <path>")
    return keys.load_private(cfg.owner_key_file)


@owner_app.command("new-key")
def owner_new_key() -> None:
    """Create the owner root key; put its public key into PROOFXIV_OWNER_PUBLIC_KEYS on the server."""
    path, public, fingerprint = keys.new_key("owner")
    config.update(owner_key_file=str(path))
    typer.echo(f"owner key {fingerprint} saved to {path}\nAdd to the server config: {public}")


@owner_app.command("lock-key")
def owner_lock_key() -> None:
    """Encrypt the owner key with a passphrase; owner commands then ask for it. Run it in your own
    terminal so that the passphrase is typed by you alone."""
    cfg = config.load()
    if not cfg.owner_key_file:
        raise typer.BadParameter("no owner key configured")
    if cfg.owner_key_file.endswith(".enc"):
        typer.echo(f"already locked: {cfg.owner_key_file}")
        return
    first = getpass.getpass("New passphrase for the owner key: ")
    if len(first) < 12:
        raise typer.BadParameter("use at least 12 characters")
    if getpass.getpass("Repeat it: ") != first:
        raise typer.BadParameter("the passphrases differ")
    target = keys.lock(cfg.owner_key_file, first)
    config.update(owner_key_file=str(target))
    typer.echo(f"owner key locked: {target} (the plain file is deleted); keep a copy of it and of the passphrase")


@owner_app.command("sign-in-link")
def owner_sign_in_link(
    login: Annotated[str, typer.Option(help="your owner login (one of PROOFXIV_OWNER_LOGINS)")],
    server: Annotated[str | None, typer.Option(help="API base URL")] = None,
) -> None:
    """A one-time link to sign in to the website as an owner, signed with the owner key."""
    cfg = config.load()
    if server:
        cfg.server = server
    with Client(cfg, token="") as anon:
        url = owner.sign_in_link(anon, _owner_key(), login)
    typer.echo(url)


@owner_app.command("grant-role")
def grant_role(login: str, role: str) -> None:
    with _owner_client() as client:
        owner.send(client, _owner_key(), action="grant_role", operator_login=login, role=role)
    typer.echo(f"granted {role} to {login}")


@owner_app.command("revoke-role")
def revoke_role(login: str, role: str, reason: str = "") -> None:
    with _owner_client() as client:
        owner.send(client, _owner_key(), action="revoke_role", operator_login=login, role=role, reason=reason)
    typer.echo(f"revoked {role} from {login}")


@owner_app.command("revoke-key")
def revoke_key(
    fingerprint: str,
    since: Annotated[str | None, typer.Option(help="distrust signatures from this ISO time")] = None,
    reason: str = "",
) -> None:
    with _owner_client() as client:
        owner.send(client, _owner_key(), action="revoke_key", key_fingerprint=fingerprint, revoke_from=since, reason=reason)
    typer.echo(f"revoked key {fingerprint}; everything it signed is being re-checked")


@owner_app.command("attest-key")
def attest_key(
    role: Annotated[str, typer.Option(help="reviewer | runner | federation")],
    public_key: Annotated[str, typer.Option(help="the key (base64); for federation: what init-instance printed")],
    login: Annotated[str | None, typer.Option(help="the operator who holds the key (reviewer, runner)")] = None,
) -> None:
    """Vouch for a key in the public owner journal, so that other ProofXiv labs accept what it
    signs: the outbox (federation), machine checks (runner), decisive reviews (reviewer)."""
    with _owner_client() as client:
        owner.send(client, _owner_key(), action="attest_key", role=role, public_key=public_key, operator_login=login)
    typer.echo(f"attested {key_fingerprint(public_key)} for {role}")


@peer_app.command("add")
def peer_add(
    url: str,
    trust: Annotated[str, typer.Option(help="index (list only) | review (check here) | full (accept the lab's checks)")] = "review",
    fingerprint: Annotated[
        str | None,
        typer.Option(help="the owner key fingerprint the lab's owner gave you: checked instead of asking"),
    ] = None,
) -> None:
    """Follow a ProofXiv lab: shows the owner key it publishes; compare its fingerprint with what the
    lab's owner told you, and confirm. That key becomes the root of trust for everything from the lab."""
    import httpx

    info = httpx.get(url.rstrip("/") + "/.well-known/proofxiv", timeout=30).raise_for_status().json()
    typer.echo(f"{info['name']} at {info['base_url']}")
    keys_listed = info.get("owner_keys") or []
    if not keys_listed:
        raise typer.BadParameter("the instance publishes no owner key")
    for i, key in enumerate(keys_listed):
        typer.echo(f"  [{i}] owner key fingerprint {key_fingerprint(key)}")
    if fingerprint is not None:
        matching = [i for i, key in enumerate(keys_listed) if key_fingerprint(key) == fingerprint.strip().lower()]
        if not matching:
            raise typer.BadParameter(f"no owner key of {info['base_url']} has the fingerprint {fingerprint}")
        index = matching[0]
    else:
        index = int(typer.prompt("Which owner key do you trust", default="0")) if len(keys_listed) > 1 else 0
        if typer.prompt(f"Follow it with trust '{trust}'? Type yes") != "yes":
            raise typer.Abort()
    with _owner_client() as client:
        owner.send(client, _owner_key(), action="add_peer", peer_url=info["base_url"], peer_owner_key=keys_listed[index], trust=trust)
    typer.echo(f"following {info['base_url']} ({trust}); the jobs loop pulls it every 15 minutes")


@peer_app.command("trust")
def peer_trust(url: str, trust: Annotated[str, typer.Argument(help="index | review | full")]) -> None:
    """Change how much this instance trusts a lab it follows (applies to what arrives next)."""
    with _owner_client() as client:
        owner.send(client, _owner_key(), action="set_peer_trust", peer_url=url.rstrip("/"), trust=trust)
    typer.echo(f"{url}: trust {trust}")


@peer_app.command("remove")
def peer_remove(url: str) -> None:
    """Stop following a lab (what came from it stays, with its origin shown)."""
    with _owner_client() as client:
        owner.send(client, _owner_key(), action="remove_peer", peer_url=url.rstrip("/"))
    typer.echo(f"no longer following {url}")


@owner_app.command("approve-prompts")
def approve_prompts(name: Annotated[list[str] | None, typer.Option(help="only these prompts")] = None) -> None:
    """Approve (sign) the local official prompts that changed."""
    with _owner_client() as client:
        approved = owner.approve_prompts(client, _owner_key(), name)
    typer.echo("approved: " + (", ".join(approved) if approved else "nothing changed"))


SecretFile = Annotated[
    Path, typer.Option("--secret", help="your private instructions (Markdown), appended to the public role text")
]


def _candidates() -> list[dict]:
    with Client(config.load()) as client:  # a public list: no token needed
        return client.get("/upstream/formal-conjectures")


@upstream_app.command("list")
def upstream_list() -> None:
    """Statements settled here that formal-conjectures still tags open."""
    candidates = _candidates()
    for c in candidates:
        seen = f" ({len(c['pull_requests_seen'])} PRs seen)" if c["pull_requests_seen"] else ""
        proof = " + formal_proof" if c["edit"]["formal_proof"] else ""
        typer.echo(f"{c['slug']}: {c['qualified']} in {c['path']}{proof}{seen}")
    typer.echo(f"{len(candidates)} candidates")


@upstream_app.command("open")
def upstream_open(
    slug: str,
    dry_run: Annotated[bool, typer.Option(help="show the edit and the PR text, open nothing")] = False,
) -> None:
    """Prepare the PR in your fork (GitHub CLI), show it, and open it only if you type yes."""
    candidate = next((c for c in _candidates() if c["slug"] == slug), None)
    if candidate is None:
        raise typer.BadParameter(f"{slug} is not a candidate (see `proofxiv owner upstream list`)")

    def confirm(preview: str) -> bool:
        typer.echo(preview)
        return typer.prompt(f"Open this pull request on {upstream.UPSTREAM} from your GitHub account? Type yes", default="no") == "yes"

    try:
        url = upstream.open_pull_request(candidate, config.workdir("upstream"), confirm, dry_run=dry_run)
    except upstream.UpstreamError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"opened {url}" if url else "nothing was opened")


@heldout_app.command("new")
def heldout_new(secret: SecretFile) -> None:
    """Commit a new held-out prompt version (its hash goes to the server, the text stays here)."""
    with _owner_client() as client:
        version, path = owner.commit_heldout(client, _owner_key(), secret.read_text())
    typer.echo(f"heldout_review v{version} committed; the text is in {path} (the weekly run needs it)")


@heldout_app.command("reveal")
def heldout_reveal(version: int) -> None:
    """Publish a held-out prompt; the transcripts of the reviews written with it become public."""
    with _owner_client() as client:
        owner.reveal_heldout(client, _owner_key(), version)
    typer.echo(f"heldout_review v{version} revealed")


@heldout_app.command("rotate")
def heldout_rotate(secret: SecretFile) -> None:
    """Commit a new version and reveal the older ones."""
    with _owner_client() as client:
        version, revealed = owner.rotate_heldout(client, _owner_key(), secret.read_text())
    typer.echo(f"heldout_review v{version} committed; revealed: {revealed or 'none'}")


@owner_app.command("set-param")
def set_param(key: str, value: str) -> None:
    with _owner_client() as client:
        owner.send(client, _owner_key(), action="set_param", param=key, value=json.loads(value))
    typer.echo(f"{key} = {value}")


@owner_app.command("hide")
def hide(target_type: str, target_id: str, reason: str) -> None:
    with _owner_client() as client:
        owner.send(client, _owner_key(), action="hide", target_type=target_type, target_id=target_id, reason=reason)
    typer.echo("hidden")


@owner_app.command("unhide")
def unhide(target_type: str, target_id: str, reason: str) -> None:
    with _owner_client() as client:
        owner.send(client, _owner_key(), action="unhide", target_type=target_type, target_id=target_id, reason=reason)
    typer.echo("restored")


@app.command("sandbox")
def sandbox_cmd(
    workspace: Path,
    command: Annotated[list[str], typer.Argument(help="the command, after --")],
    minutes: Annotated[float, typer.Option(help="time limit")] = 10,
    ram_gb: Annotated[float, typer.Option(help="memory limit")] = 4,
) -> None:
    """Run a command on a task's files in the sandbox: no network, files/ read-only, writes only
    to scratch/. Usage: proofxiv sandbox <workspace> -- <command...>"""
    cfg = config.load()
    backend = cfg.sandbox if cfg.sandbox != "none" else "bwrap"
    raise typer.Exit(subagents.run_sandboxed(workspace, command, minutes, ram_gb, backend))


@app.command("check-output")
def check_output(workspace: Path) -> None:
    """Validate <workspace>/output.json against the task's schema."""
    problem = subagents.check_output(workspace)
    if problem:
        typer.echo(problem, err=True)
        raise typer.Exit(1)
    typer.echo("valid")


@tasks_app.command("prepare")
def tasks_prepare(
    kinds: Annotated[str, typer.Option(help="comma-separated task kinds")] = "novelty_consistency,novelty_audit,statement_audit,anchor_review,heldout_review",
    limit: Annotated[int, typer.Option(help="how many tasks to lease")] = 10,
) -> None:
    """Start (or continue) a weekly run, lease tasks and prepare a workspace per task."""
    with _client() as client:
        prepared = subagents.prepare(client, kinds.split(","), limit)
    for spec, ws in prepared:
        target = spec.target.paper_id or spec.target.problem_id or ""
        typer.echo(f"{spec.kind.value}{' (adversarial)' if spec.adversarial else ''}	{target}	{ws}")
    if not prepared:
        typer.echo("no tasks to lease")


@tasks_app.command("submit")
def tasks_submit(
    workspace: Path,
    transcript: Annotated[Path, typer.Argument(help="the subagent's transcript (JSONL)")],
    model: Annotated[str, typer.Option(help="the model the subagent ran on")] = "claude-opus-5-5",
) -> None:
    """Check the answer for leaked secrets, sign it and send it."""
    problem = subagents.check_output(workspace)
    if problem:
        raise typer.BadParameter(f"output.json: {problem}")
    with _client() as client:
        out = subagents.submit(client, _private_key(), workspace, transcript, model)
    _echo_json(out)


@tasks_app.command("release")
def tasks_release(workspace: Path, reason: str) -> None:
    """Give a leased task back to the queue."""
    with _client() as client:
        subagents.release(client, workspace, reason)
    typer.echo("released")


@tasks_app.command("finish")
def tasks_finish() -> None:
    """Finish the weekly run started by prepare."""
    with _client() as client:
        typer.echo(f"finished {subagents.finish(client)}")


@tasks_app.command("install-agent")
def tasks_install_agent(
    project_dir: Annotated[Path, typer.Argument(help="the directory you run Claude Code in")] = Path("."),
) -> None:
    """Install the proofxiv-reviewer subagent and its guard into <project_dir>/.claude/agents."""
    path = subagents.install_agent(project_dir.resolve())
    typer.echo(f"installed {path}; start a new Claude Code session there to use it")


def main() -> None:
    try:
        app()
    except ApiError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
