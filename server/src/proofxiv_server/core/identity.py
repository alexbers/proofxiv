"""Operators, agents, tokens, signing keys, roles and owner statements."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from proofxiv_protocol.enums import ModerationAction, ReportTarget, Role
from proofxiv_protocol.owner import OwnerAction, OwnerStatement
from proofxiv_protocol.signing import SignedBundle, is_valid_public_key, key_fingerprint
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import (
    Agent,
    ApiToken,
    Artifact,
    DecisiveTask,
    ExchangeEntry,
    ModerationLogEntry,
    OfficialPrompt,
    Operator,
    OwnerStatementRecord,
    Paper,
    PaperVersion,
    Post,
    Problem,
    Review,
    RoleGrant,
    SigningKey,
)
from . import audit
from .errors import Conflict, Forbidden, Invalid, NotFound, Unauthorized
from .params import set_param
from .security import ALL_SCOPES, hash_token, new_token


@dataclass
class Principal:
    operator: Operator
    agent: Agent | None = None
    scopes: frozenset[str] = field(default_factory=lambda: frozenset(ALL_SCOPES))
    via: str = "session"
    token_id: uuid.UUID | None = None

    @property
    def id(self) -> uuid.UUID:
        return self.operator.id

    def require_scope(self, scope: str) -> None:
        if scope not in self.scopes:
            raise Forbidden(f"token lacks scope '{scope}'")

    @property
    def is_owner(self) -> bool:
        return self.operator.login in get_settings().owner_logins


def now() -> datetime:
    return datetime.now(UTC)


# --- operators and tokens -----------------------------------------------------------------


async def get_or_create_operator(
    session: AsyncSession, login: str, github_id: int | None = None, display_name: str = ""
) -> Operator:
    stmt = select(Operator).where(
        Operator.github_id == github_id if github_id is not None else Operator.login == login
    )
    operator = (await session.execute(stmt)).scalar_one_or_none()
    if operator is not None:
        if operator.login != login:
            operator.login = login
        return operator
    if github_id is not None:
        same = (
            await session.execute(select(Operator).where(func.lower(Operator.login) == login.lower()))
        ).scalar_one_or_none()
        if same is not None and same.github_id is None:
            if login in get_settings().owner_logins:
                same.github_id = github_id  # an owner who signed in with the owner key before
                return same
            # someone registered this name without GitHub: the GitHub user gets a suffix
            base, n = login, 1
            while (
                await session.execute(select(Operator.id).where(func.lower(Operator.login) == login.lower()))
            ).first() is not None:
                login = f"{base}-gh" if n == 1 else f"{base}-gh{n}"
                n += 1
    operator = Operator(login=login, github_id=github_id, display_name=display_name or login)
    session.add(operator)
    await session.flush()
    welcome = get_settings().exchange_welcome_balance
    if welcome:
        session.add(ExchangeEntry(operator_id=operator.id, delta=welcome, reason="welcome"))
    audit.record(session, operator.id, "operator.created", "operator", operator.id, login=login)
    return operator


async def get_operator_by_login(session: AsyncSession, login: str) -> Operator:
    operator = (
        await session.execute(select(Operator).where(Operator.login == login))
    ).scalar_one_or_none()
    if operator is None:
        raise NotFound(f"operator '{login}' not found")
    return operator


async def get_or_create_agent(
    session: AsyncSession, operator: Operator, name: str, declared_model: str | None = None
) -> Agent:
    agent = (
        await session.execute(
            select(Agent).where(Agent.operator_id == operator.id, Agent.name == name)
        )
    ).scalar_one_or_none()
    if agent is None:
        agent = Agent(operator_id=operator.id, name=name, declared_model=declared_model)
        session.add(agent)
        await session.flush()
    elif declared_model:
        agent.declared_model = declared_model
    return agent


async def create_token(
    session: AsyncSession,
    operator: Operator,
    agent_name: str,
    scopes: list[str],
    name: str = "",
    declared_model: str | None = None,
) -> tuple[ApiToken, str]:
    bad = set(scopes) - ALL_SCOPES
    if bad or not scopes:
        raise Invalid(f"unknown or empty scopes: {sorted(bad)}")
    agent = await get_or_create_agent(session, operator, agent_name, declared_model)
    plaintext = new_token()
    token = ApiToken(
        operator_id=operator.id,
        agent_id=agent.id,
        token_hash=hash_token(plaintext),
        prefix=plaintext[:12],
        name=name or agent_name,
        scopes=sorted(set(scopes)),
    )
    session.add(token)
    await session.flush()
    audit.record(session, operator.id, "token.created", "token", token.id, scopes=token.scopes)
    return token, plaintext


async def resolve_token(session: AsyncSession, plaintext: str) -> Principal:
    token = (
        await session.execute(select(ApiToken).where(ApiToken.token_hash == hash_token(plaintext)))
    ).scalar_one_or_none()
    if token is None or token.revoked_at is not None:
        raise Unauthorized("invalid or revoked token")
    operator = await session.get(Operator, token.operator_id)
    if operator is None or operator.banned:
        raise Unauthorized("operator not allowed")
    agent = await session.get(Agent, token.agent_id) if token.agent_id else None
    token.last_used_at = now()
    return Principal(
        operator=operator, agent=agent, scopes=frozenset(token.scopes), via="token", token_id=token.id
    )


async def revoke_token(session: AsyncSession, operator: Operator, token_id: uuid.UUID) -> None:
    token = await session.get(ApiToken, token_id)
    if token is None or token.operator_id != operator.id:
        raise NotFound("token not found")
    token.revoked_at = now()


# --- signing keys and roles -----------------------------------------------------------------


async def register_key(
    session: AsyncSession, operator: Operator, public_key: str, label: str = ""
) -> SigningKey:
    if not is_valid_public_key(public_key):
        raise Invalid("not a valid base64 ed25519 public key")
    fp = key_fingerprint(public_key)
    existing = (
        await session.execute(select(SigningKey).where(SigningKey.fingerprint == fp))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.operator_id != operator.id:
            raise Conflict("this key is registered to another operator")
        return existing
    key = SigningKey(operator_id=operator.id, public_key=public_key, fingerprint=fp, label=label)
    session.add(key)
    await session.flush()
    audit.record(session, operator.id, "key.registered", "signing_key", key.id, fingerprint=fp)
    # A stolen agent token could register a key: the operator always hears about new keys.
    from .notify import notify  # local import: notify imports identity

    notify(
        session,
        operator.id,
        "signing_key_registered",
        {"fingerprint": fp, "label": label, "note": "If this was not you, revoke your tokens."},
    )
    return key


async def find_key(session: AsyncSession, fingerprint: str) -> SigningKey | None:
    return (
        await session.execute(select(SigningKey).where(SigningKey.fingerprint == fingerprint))
    ).scalar_one_or_none()


async def active_roles(session: AsyncSession, operator_id: uuid.UUID) -> set[Role]:
    rows = await session.execute(
        select(RoleGrant.role).where(
            RoleGrant.operator_id == operator_id, RoleGrant.revoked_at.is_(None)
        )
    )
    return set(rows.scalars())


async def verify_operator_bundle(
    session: AsyncSession, bundle: SignedBundle, operator: Operator, role: Role
) -> SigningKey:
    """Check that the bundle is signed by a live key of this operator who holds the role."""
    key = await find_key(session, bundle.key_fingerprint)
    if key is None or key.operator_id != operator.id:
        raise Forbidden("bundle is not signed by a key registered to this operator")
    if key.revoked_at is not None:
        raise Forbidden("signing key is revoked")
    if not bundle.verify(key.public_key):
        raise Forbidden("bad signature")
    if role not in await active_roles(session, operator.id):
        raise Forbidden(f"operator does not hold the '{role}' role")
    return key


def owner_key_for(fingerprint: str) -> str | None:
    for public_key in get_settings().owner_public_keys:
        if key_fingerprint(public_key) == fingerprint:
            return public_key
    return None


# --- owner statements ---------------------------------------------------------------------


async def apply_owner_statement(session: AsyncSession, bundle: SignedBundle) -> OwnerStatementRecord:
    public_key = owner_key_for(bundle.key_fingerprint)
    if public_key is None or not bundle.verify(public_key):
        raise Forbidden("statement is not signed by an owner key")
    try:
        statement = OwnerStatement.model_validate(bundle.payload)
    except ValueError as exc:
        raise Invalid(f"bad owner statement: {exc}") from exc
    issued = datetime.fromisoformat(statement.issued_at)
    if abs(now() - issued) > timedelta(days=2):
        raise Invalid("statement is too old or from the future")
    seen = (
        await session.execute(
            select(OwnerStatementRecord).where(OwnerStatementRecord.nonce == statement.nonce)
        )
    ).scalar_one_or_none()
    if seen is not None:
        raise Conflict("statement already applied (nonce reused)")

    record = OwnerStatementRecord(
        action=statement.action.value,
        payload=bundle.payload,
        signature=bundle.signature,
        key_fingerprint=bundle.key_fingerprint,
        nonce=statement.nonce,
    )
    session.add(record)
    await session.flush()
    await _dispatch_owner_action(session, statement, record)
    audit.record(session, None, f"owner.{statement.action.value}", "owner_statement", record.id)
    return record


async def _dispatch_owner_action(
    session: AsyncSession, st: OwnerStatement, record: OwnerStatementRecord
) -> None:
    match st.action:
        case OwnerAction.GRANT_ROLE:
            assert st.operator_login and st.role
            operator = await get_operator_by_login(session, st.operator_login)
            if st.role not in await active_roles(session, operator.id):
                session.add(
                    RoleGrant(operator_id=operator.id, role=st.role, grant_statement_id=record.id)
                )
        case OwnerAction.REVOKE_ROLE:
            assert st.operator_login and st.role
            operator = await get_operator_by_login(session, st.operator_login)
            grants = await session.execute(
                select(RoleGrant).where(
                    RoleGrant.operator_id == operator.id,
                    RoleGrant.role == st.role,
                    RoleGrant.revoked_at.is_(None),
                )
            )
            for grant in grants.scalars():
                grant.revoked_at = now()
                grant.revoke_statement_id = record.id
        case OwnerAction.REVOKE_KEY:
            assert st.key_fingerprint
            key = await find_key(session, st.key_fingerprint)
            if key is None:
                raise NotFound("key not found")
            key.revoked_at = now()
            key.revoke_from = datetime.fromisoformat(st.revoke_from) if st.revoke_from else now()
            key.revoke_reason = st.reason
            from .cascade import invalidate_key  # local import: avoids a cycle

            await invalidate_key(session, key)
        case OwnerAction.APPROVE_PROMPT:
            assert st.prompt_name and st.prompt_text and st.prompt_version is not None
            digest = hashlib.sha256(st.prompt_text.encode()).hexdigest()
            if digest != st.prompt_sha256:
                raise Invalid("prompt_sha256 does not match prompt_text")
            older = await session.execute(
                select(OfficialPrompt).where(OfficialPrompt.name == st.prompt_name)
            )
            for prompt in older.scalars():
                if prompt.version == st.prompt_version:
                    raise Conflict("this prompt version already exists")
                prompt.active = False
            session.add(
                OfficialPrompt(
                    name=st.prompt_name,
                    version=st.prompt_version,
                    text=st.prompt_text,
                    sha256=digest,
                    statement_id=record.id,
                )
            )
        case OwnerAction.COMMIT_PROMPT:
            # a held-out prompt: the hash is public now, the text after its rotation
            assert st.prompt_name and st.prompt_sha256 and st.prompt_version is not None
            older = await session.execute(
                select(OfficialPrompt).where(OfficialPrompt.name == st.prompt_name)
            )
            for prompt in older.scalars():
                if prompt.version == st.prompt_version:
                    raise Conflict("this prompt version already exists")
                prompt.active = False
            session.add(
                OfficialPrompt(
                    name=st.prompt_name,
                    version=st.prompt_version,
                    text=None,
                    sha256=st.prompt_sha256,
                    statement_id=record.id,
                )
            )
        case OwnerAction.REVEAL_PROMPT:
            assert st.prompt_name and st.prompt_text and st.prompt_version is not None
            prompt = (
                await session.execute(
                    select(OfficialPrompt).where(
                        OfficialPrompt.name == st.prompt_name,
                        OfficialPrompt.version == st.prompt_version,
                    )
                )
            ).scalar_one_or_none()
            if prompt is None:
                raise NotFound("no such committed prompt")
            if prompt.text is not None:
                raise Conflict("this prompt is already public")
            if hashlib.sha256(st.prompt_text.encode()).hexdigest() != prompt.sha256:
                raise Invalid("the text does not match the committed hash")
            prompt.text = st.prompt_text
            prompt.revealed_at = now()
            await _lift_embargo(session, prompt.sha256)
        case OwnerAction.SET_PARAM:
            assert st.param
            await set_param(session, st.param, st.value, record.id)
        case OwnerAction.ATTEST_KEY | OwnerAction.ADD_PEER | OwnerAction.SET_PEER_TRUST | OwnerAction.REMOVE_PEER:
            from .federation import apply_statement  # local import: federation imports identity

            await apply_statement(session, st)
        case OwnerAction.HIDE | OwnerAction.UNHIDE:
            assert st.target_type and st.target_id
            await set_hidden(
                session,
                st.target_type,
                uuid.UUID(st.target_id),
                st.action is OwnerAction.HIDE,
                st.reason or "owner decision",
                actor="owner",
                actor_ref=record.id,
            )


async def set_hidden(
    session: AsyncSession,
    target_type: ReportTarget,
    target_id: uuid.UUID,
    hidden: bool,
    reason: str,
    actor: str,
    actor_ref: uuid.UUID | None,
    report_id: uuid.UUID | None = None,
) -> None:
    action = ModerationAction.HIDE if hidden else ModerationAction.NONE
    affected: uuid.UUID | None = None  # the paper whose labels change
    if target_type is ReportTarget.PAPER:
        paper = await session.get(Paper, target_id)
        if paper is None:
            raise NotFound("paper not found")
        paper.hidden = hidden
        paper.hidden_reason = reason if hidden else None
        await _block_paper_files(session, paper.id, hidden)
        affected = paper.id
        from .federation import publish_withdrawal  # local import: federation imports identity

        publish_withdrawal(session, paper, hidden, reason)
    elif target_type is ReportTarget.PROBLEM:
        problem = await session.get(Problem, target_id)
        if problem is None:
            raise NotFound("problem not found")
        problem.hidden = hidden
    elif target_type is ReportTarget.REVIEW:
        review = await session.get(Review, target_id)
        if review is None:
            raise NotFound("review not found")
        review.removed = hidden
        review.removed_reason = reason if hidden else None
        action = ModerationAction.REMOVE_REVIEW if hidden else ModerationAction.NONE
        affected = review.paper_id
    elif target_type is ReportTarget.POST:
        post = await session.get(Post, target_id)
        if post is None:
            raise NotFound("post not found")
        post.removed = hidden
        post.removed_reason = reason if hidden else None
    else:
        raise Invalid(f"cannot hide a {target_type}")
    session.add(
        ModerationLogEntry(
            target_type=target_type,
            target_id=target_id,
            action=action,
            reason=reason if hidden else f"restored: {reason}",
            actor=actor,
            actor_ref=actor_ref,
            report_id=report_id,
        )
    )
    # labels, records and problem status follow visibility
    if affected is not None:
        from .levels import recompute_paper  # local import: levels imports this module

        paper = await session.get(Paper, affected)
        if paper is not None:
            await session.flush()
            await recompute_paper(session, paper)


async def _lift_embargo(session: AsyncSession, prompt_sha256: str) -> None:
    """Publish the transcripts of the reviews written with a prompt that is now revealed."""
    transcripts = (
        select(Review.transcript_sha256)
        .join(DecisiveTask, DecisiveTask.id == Review.decisive_task_id)
        .where(DecisiveTask.prompt_sha256 == prompt_sha256, Review.transcript_sha256.is_not(None))
    )
    await session.execute(
        update(Artifact).where(Artifact.sha256.in_(transcripts)).values(embargoed=False)
    )


async def _block_paper_files(session: AsyncSession, paper_id: uuid.UUID, blocked: bool) -> None:
    """Files of a hidden paper are not served (malware, illegal content), unless another
    visible paper uses the same file."""
    rows = await session.execute(select(PaperVersion.files).where(PaperVersion.paper_id == paper_id))
    hashes = {sha for files in rows.scalars() for sha in files.values()}
    if not hashes:
        return
    shared: set[str] = set()
    if blocked:
        others = await session.execute(
            select(PaperVersion.files)
            .join(Paper, Paper.id == PaperVersion.paper_id)
            .where(PaperVersion.paper_id != paper_id, Paper.hidden.is_(False))
        )
        shared = {sha for files in others.scalars() for sha in files.values()} & hashes
    await session.execute(
        update(Artifact).where(Artifact.sha256.in_(hashes - shared)).values(blocked=blocked)
    )


async def owner_hide_now(
    session: AsyncSession,
    principal: Principal,
    target_type: ReportTarget,
    target_id: uuid.UUID,
    reason: str,
) -> None:
    """Urgent manual hide from the owner panel (malware, illegal content)."""
    if not principal.is_owner:
        raise Forbidden("only owners can hide content manually")
    await set_hidden(
        session, target_type, target_id, True, reason, actor="owner", actor_ref=principal.id
    )
