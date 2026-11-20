import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";

import { CopyButton } from "@/components/me/CopyButton";
import { Chip, Empty, Hash, MetaChip, PageTitle, Section, Time } from "@/components/ui";
import type { OwnerJournalEntryOut, PromptOut } from "@/lib/api";
import { OWNER_ACTIONS, peerScopeText, roleMeta } from "@/lib/labels";
import { load, loadOr, serverApi } from "@/lib/server-api";

export const metadata: Metadata = {
  title: "Trusted operators",
  description:
    "The root of trust of ProofXiv (owner keys), who runs the platform's decisive checks, their roles, keys and statistics, and the signed owner journal.",
};

function journalSummary(entry: OwnerJournalEntryOut): string {
  const p = entry.payload;
  const str = (k: string) => (typeof p[k] === "string" || typeof p[k] === "number" ? String(p[k]) : "");
  switch (entry.action) {
    case "grant_role":
    case "revoke_role":
      return `${str("role")} → ${str("operator_login")}`;
    case "revoke_key":
      return `key ${str("key_fingerprint").slice(0, 16)}${str("reason") ? ` — ${str("reason")}` : ""}`;
    case "approve_prompt":
    case "reveal_prompt":
      return `${str("prompt_name")} v${str("prompt_version")}`;
    case "commit_prompt":
      return `${str("prompt_name")} v${str("prompt_version")} — sha256 ${str("prompt_sha256").slice(0, 16)}…`;
    case "set_param":
      return `${str("param")} = ${JSON.stringify(p.value)}`;
    case "attest_key":
      return `${str("role")}${str("operator_login") ? ` → ${str("operator_login")}` : ""}`;
    case "add_peer": {
      const scoped = Array.isArray(p.peer_domains) || str("peer_min_level");
      const scope = scoped ? `; ${peerScopeText(p.peer_domains as string[] | null, str("peer_min_level"))}` : "";
      return `${str("peer_url")} — trust ${str("trust")}${scope}`;
    }
    case "set_peer_trust":
      return `${str("peer_url")} — trust ${str("trust")}`;
    case "set_peer_scope":
      return `${str("peer_url")} — ${peerScopeText(p.peer_domains as string[] | null, str("peer_min_level"))}`;
    case "remove_peer":
      return str("peer_url");
    case "hide":
    case "unhide":
      return `${str("target_type")} ${str("target_id").slice(0, 8)}${str("reason") ? ` — ${str("reason")}` : ""}`;
    default:
      return "";
  }
}

function PromptHead({ prompt }: { prompt: PromptOut }) {
  return (
    <>
      <code className="inline font-semibold">{prompt.name}</code>
      <span>v{prompt.version}</span>
      {prompt.active ? (
        <Chip tone="green" description="The weekly trusted run uses this version now.">
          in use
        </Chip>
      ) : (
        <Chip tone="gray" description="Replaced by a newer version.">
          retired
        </Chip>
      )}
      {prompt.held_out ? (
        <Chip
          tone="purple"
          emoji="🔒"
          description="Committed by hash only; the text is published after the prompt is rotated, so papers cannot be tuned against it."
        >
          held out
        </Chip>
      ) : null}
      {prompt.revealed_at ? (
        <span className="text-xs text-muted">
          revealed <Time iso={prompt.revealed_at} />
        </span>
      ) : null}
    </>
  );
}

/** One prompt version on one line; the text unfolds below it. */
function PromptItem({ prompt }: { prompt: PromptOut }) {
  if (prompt.text === null) {
    return (
      <li className="flex flex-wrap items-center gap-x-2 gap-y-1 px-4 py-2.5 text-sm">
        <span aria-hidden="true" className="inline-block w-3" />
        <PromptHead prompt={prompt} />
        <span className="text-muted">
          Held out — hash committed: <Hash value={prompt.sha256} length={16} />, text published after rotation
        </span>
      </li>
    );
  }
  return (
    <li className="px-4 py-2.5 text-sm">
      <details className="group">
        <summary className="flex list-none flex-wrap items-center gap-x-2 gap-y-1 [&::-webkit-details-marker]:hidden">
          <span aria-hidden="true" className="inline-block w-3 text-[0.65rem] text-muted group-open:rotate-90">
            ▶
          </span>
          <PromptHead prompt={prompt} />
          <span className="text-xs text-muted sm:ml-auto">
            sha256 <Hash value={prompt.sha256} length={16} />
            <span className="sr-only"> (show the text)</span>
          </span>
        </summary>
        <pre className="mt-2 max-h-80 overflow-auto rounded-md border border-line bg-surface-2 p-2 font-mono text-[0.72rem] leading-snug whitespace-pre-wrap">
          {prompt.text}
        </pre>
      </details>
    </li>
  );
}

export default async function TrustedPage() {
  await connection(); // always render with live data
  const [root, operators, prompts, journal] = await Promise.all([
    load(serverApi.GET("/api/v1/root-of-trust")),
    load(serverApi.GET("/api/v1/trusted-operators")),
    loadOr(serverApi.GET("/api/v1/prompts"), []),
    load(serverApi.GET("/api/v1/owner/journal")),
  ]);
  // in use first, then by name and newest version
  const sortedPrompts = [...prompts].sort(
    (a, b) => Number(b.active) - Number(a.active) || a.name.localeCompare(b.name) || b.version - a.version,
  );
  const ownerFingerprints = new Set(root.keys.map((k) => k.fingerprint));

  return (
    <div className="space-y-12">
      <PageTitle
        subtitle={
          <>
            Trusted operators run the weekly trusted script: report decisions, machine checks, platform reviews and literature
            confirmation. The owners appoint them with statements signed by an owner key, so the chain of trust can be checked
            without trusting the server. Every result they produce is signed and published with its transcript, and any of
            them can be challenged with a report.
          </>
        }
      >
        Trusted operators
      </PageTitle>

      <Section
        id="root"
        title="Root of trust"
        description={
          <>
            The owners&apos; public keys (ed25519, base64). They sign every statement in the{" "}
            <a href="#journal">owner journal</a>: role grants, key revocations, official prompts, parameters and manual
            hides. To check a statement without trusting this server, verify its signature against one of these keys; each
            statement names the key by its fingerprint.
          </>
        }
      >
        <div className="card space-y-4 p-4">
          {root.keys.length ? (
            <ul className="space-y-3">
              {root.keys.map((key) => (
                <li key={key.fingerprint} className="space-y-1 text-sm">
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="text-muted">Fingerprint</span>
                    <code className="inline break-all">{key.fingerprint}</code>
                  </p>
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="text-muted">Public key</span>
                    <code className="inline break-all select-all">{key.public_key}</code>
                    <CopyButton text={key.public_key} label="Copy key" />
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">No owner keys are configured on this server.</p>
          )}
          <p className="border-t border-line pt-3 text-sm">
            <span className="text-muted">Owner accounts on the website: </span>
            {root.logins.length
              ? root.logins.map((login, i) => (
                  <span key={login}>
                    {i ? ", " : ""}
                    <Link href={`/u/${encodeURIComponent(login)}`}>{login}</Link>
                  </span>
                ))
              : "none"}
            <span className="text-muted">
              {" "}
              — they can use the emergency hide; everything else they change is signed with an owner key.
            </span>
          </p>
        </div>
      </Section>

      <Section id="operators" title="Operators" count={operators.length}>
        {operators.length ? (
          <div className="grid gap-4 md:grid-cols-2">
            {operators.map((op) => (
              <article key={op.login} className="card p-4" aria-labelledby={`op-${op.login}`}>
                <h3 id={`op-${op.login}`} className="text-lg font-semibold">
                  <Link href={`/u/${encodeURIComponent(op.login)}`}>{op.login}</Link>
                </h3>
                <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Active roles">
                  {op.roles.length ? (
                    op.roles.map((role) => (
                      <li key={role}>
                        <MetaChip meta={roleMeta(role)} />
                      </li>
                    ))
                  ) : (
                    <li className="text-sm text-muted">No active roles</li>
                  )}
                </ul>
                <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
                  <div className="rounded-lg bg-surface-2 p-2">
                    <dt className="text-xs text-muted">Decisive tasks done</dt>
                    <dd className="text-lg font-semibold tabular-nums">{op.stats.decisive_done}</dd>
                  </div>
                  <div className="rounded-lg bg-surface-2 p-2">
                    <dt className="text-xs text-muted">Machine-check runs</dt>
                    <dd className="text-lg font-semibold tabular-nums">{op.stats.runs}</dd>
                  </div>
                </dl>
                <h4 className="mt-4 text-sm font-semibold">Signing keys</h4>
                {op.keys.length ? (
                  <ul className="mt-1 space-y-1 text-sm">
                    {op.keys.map((key) => (
                      <li key={key.fingerprint} className="flex flex-wrap items-center gap-2">
                        <Hash value={key.fingerprint} length={32} />
                        {key.label ? <span className="text-muted">{key.label}</span> : null}
                        {key.revoked_at ? (
                          <Chip tone="red" description="Results signed with this key after the revocation point are re-checked.">
                            revoked <Time iso={key.revoked_at} />
                          </Chip>
                        ) : (
                          <Chip tone="green">active</Chip>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-sm text-muted">No keys registered.</p>
                )}
                <h4 className="mt-4 text-sm font-semibold">Role history</h4>
                <ul className="mt-1 space-y-1 text-sm">
                  {op.history.map((h, i) => (
                    <li key={i}>
                      <span className="font-medium">{roleMeta(h.role).text}</span>: granted <Time iso={h.granted_at} />
                      {h.revoked_at ? (
                        <>
                          , revoked <Time iso={h.revoked_at} />
                        </>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        ) : (
          <Empty>No trusted operators yet.</Empty>
        )}
      </Section>

      <Section
        id="prompts"
        title="Official prompts"
        count={prompts.length}
        description={
          <>
            Every platform review is run with an official prompt approved in the <a href="#journal">owner journal</a>, and
            its signed result names the prompt&apos;s sha256. A held-out prompt is committed by its hash only; its text is
            published when it is rotated. <Link href="/about#held-out">Why some prompts are held out</Link>
          </>
        }
      >
        {sortedPrompts.length ? (
          <ul className="card divide-y divide-line">
            {sortedPrompts.map((prompt) => (
              <PromptItem key={`${prompt.name}-${prompt.version}`} prompt={prompt} />
            ))}
          </ul>
        ) : (
          <Empty>No official prompts are approved yet.</Empty>
        )}
      </Section>

      <Section
        id="journal"
        title="Owner journal"
        count={journal.length}
        description="Statements signed by an owner key: role grants and revocations, key revocations, official prompt versions, parameters and manual hides. Newest first."
      >
        {journal.length ? (
          <ol className="card divide-y divide-line">
            {journal.map((entry) => (
              <li key={entry.id} className="px-4 py-3 text-sm">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="font-semibold">{OWNER_ACTIONS[entry.action] ?? entry.action}</span>
                  <span className="min-w-0 break-words text-muted">{journalSummary(entry)}</span>
                  <span className="ml-auto text-xs whitespace-nowrap text-muted">
                    <Time iso={entry.created_at} withTime />
                  </span>
                </div>
                <p className="mt-1 flex flex-wrap items-center gap-1 text-xs text-muted">
                  Signed by owner key <Hash value={entry.key_fingerprint} length={32} />
                  {ownerFingerprints.has(entry.key_fingerprint) ? (
                    <Chip tone="green" emoji="✓" description="The fingerprint matches one of the owner keys listed above.">
                      root key
                    </Chip>
                  ) : (
                    <Chip tone="amber" description="This key is not among the owner keys currently configured on the server.">
                      key no longer listed
                    </Chip>
                  )}
                  · signature <Hash value={entry.signature} length={20} />
                </p>
                <details className="mt-1">
                  <summary className="text-xs text-muted">Signed payload</summary>
                  <pre className="mt-1 max-h-80 overflow-auto rounded-md border border-line bg-surface-2 p-2 font-mono text-[0.72rem] leading-snug whitespace-pre-wrap">
                    {JSON.stringify(entry.payload, null, 2)}
                  </pre>
                </details>
              </li>
            ))}
          </ol>
        ) : (
          <Empty>The journal is empty.</Empty>
        )}
      </Section>
    </div>
  );
}
