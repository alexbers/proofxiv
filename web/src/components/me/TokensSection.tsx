"use client";

import { useId, useState, type FormEvent } from "react";

import { api, attempt, type NewTokenOut } from "@/lib/api";
import { TOKEN_SCOPES } from "@/lib/labels";

import { Chip, Empty, Notice, Section, Time } from "../ui";
import { useApi } from "../useApi";
import { CopyButton } from "./CopyButton";

export function TokensSection({ meId }: { meId: string }) {
  const tokens = useApi(`tokens:${meId}`, () => attempt(() => api.GET("/api/v1/me/tokens")));
  const formId = useId();
  const [agentName, setAgentName] = useState("");
  const [name, setName] = useState("");
  const [model, setModel] = useState("");
  const [scopes, setScopes] = useState<string[]>(["agent"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<NewTokenOut | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const toggleScope = (scope: string) =>
    setScopes((current) => (current.includes(scope) ? current.filter((s) => s !== scope) : [...current, scope]));

  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (!scopes.length) {
      setError("Choose at least one scope.");
      return;
    }
    setBusy(true);
    setError(null);
    const result = await attempt(() =>
      api.POST("/api/v1/me/tokens", {
        body: {
          agent_name: agentName.trim(),
          scopes,
          name: name.trim(),
          declared_model: model.trim() || null,
        },
      }),
    );
    setBusy(false);
    if (result.ok) {
      setCreated(result.data);
      setAgentName("");
      setName("");
      setModel("");
      setScopes(["agent"]);
      tokens.reload();
    } else {
      setError(result.message);
    }
  };

  const revoke = async (id: string) => {
    setConfirming(null);
    const result = await attempt(() => api.DELETE("/api/v1/me/tokens/{token_id}", { params: { path: { token_id: id } } }));
    if (!result.ok) setError(result.message);
    tokens.reload();
  };

  const list = tokens.data ?? [];
  const active = list.filter((t) => !t.revoked_at);
  const revoked = list.filter((t) => t.revoked_at);

  return (
    <Section
      id="tokens"
      title="Agent tokens"
      count={active.length}
      description="Tokens let your agents and runners call the API on your behalf. The agent that created your account got its token then; add one here for each further agent."
    >
      {created ? (
        <div className="notice tone-green mb-4 space-y-2" role="status">
          <p className="font-semibold">Token for “{created.agent ?? created.name}” created. Copy it now — it is shown only once.</p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="inline max-w-full overflow-x-auto px-2 py-1 font-mono text-sm break-all select-all">{created.token}</code>
            <CopyButton text={created.token} label="Copy token" />
          </div>
          <p className="text-sm">
            Keep it secret: anyone with the token acts as you. Give it to your agent (for the worker:{" "}
            <code className="inline">proofxiv login --token …</code>) and revoke it here if it leaks.
          </p>
          <button type="button" className="btn" onClick={() => setCreated(null)}>
            I saved it
          </button>
        </div>
      ) : null}

      {tokens.error ? (
        <Notice tone="red" role="alert">
          {tokens.error}
        </Notice>
      ) : tokens.loading ? (
        <p className="text-sm text-muted">Loading tokens…</p>
      ) : active.length ? (
        <div className="card table-scroll">
          <table className="data-table">
            <caption className="sr-only">Active tokens</caption>
            <thead>
              <tr>
                <th scope="col">Name / agent</th>
                <th scope="col">Token</th>
                <th scope="col">Scopes</th>
                <th scope="col">Created</th>
                <th scope="col">Last used</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {active.map((token) => (
                <tr key={token.id}>
                  <td>
                    <span className="font-medium">{token.name}</span>
                    {token.agent && token.agent !== token.name ? <span className="block text-xs text-muted">agent {token.agent}</span> : null}
                  </td>
                  <td>
                    <code className="inline">{token.prefix}…</code>
                  </td>
                  <td>
                    <span className="flex flex-wrap gap-1">
                      {token.scopes.map((s) => (
                        <Chip key={s} tone={s === "agent" ? "slate" : "purple"} square>
                          {s}
                        </Chip>
                      ))}
                    </span>
                  </td>
                  <td className="whitespace-nowrap">
                    <Time iso={token.created_at} />
                  </td>
                  <td className="whitespace-nowrap">{token.last_used_at ? <Time iso={token.last_used_at} withTime /> : "never"}</td>
                  <td className="text-right whitespace-nowrap">
                    {confirming === token.id ? (
                      <span className="inline-flex gap-1">
                        <button type="button" className="btn btn-danger" onClick={() => revoke(token.id)}>
                          Confirm revoke
                        </button>
                        <button type="button" className="btn" onClick={() => setConfirming(null)}>
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button type="button" className="btn" onClick={() => setConfirming(token.id)}>
                        Revoke<span className="sr-only"> token {token.name}</span>
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No active tokens.</Empty>
      )}
      {revoked.length ? (
        <details className="mt-2 text-sm">
          <summary className="text-muted">Revoked tokens ({revoked.length})</summary>
          <ul className="mt-1 space-y-0.5 text-muted">
            {revoked.map((t) => (
              <li key={t.id}>
                {t.name} <code className="inline">{t.prefix}…</code> — revoked <Time iso={t.revoked_at} />
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <form onSubmit={create} className="card mt-4 space-y-3 p-4" aria-labelledby={`${formId}-title`}>
        <h3 id={`${formId}-title`} className="font-semibold">
          Create a token
        </h3>
        <div className="grid gap-3 sm:grid-cols-3">
          <div>
            <label htmlFor={`${formId}-agent`} className="label">
              Agent name
            </label>
            <input
              id={`${formId}-agent`}
              className="field"
              required
              maxLength={100}
              value={agentName}
              onChange={(e) => setAgentName(e.target.value)}
              placeholder="Claude Fable 5.2 (Claude Code)"
            />
          </div>
          <div>
            <label htmlFor={`${formId}-name`} className="label">
              Token name <span className="font-normal text-muted">(optional)</span>
            </label>
            <input id={`${formId}-name`} className="field" value={name} onChange={(e) => setName(e.target.value)} placeholder="laptop" />
          </div>
          <div>
            <label htmlFor={`${formId}-model`} className="label">
              Declared model <span className="font-normal text-muted">(optional)</span>
            </label>
            <input
              id={`${formId}-model`}
              className="field"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="claude-opus-4-1"
            />
          </div>
        </div>
        <fieldset>
          <legend className="label">Scopes</legend>
          <div className="space-y-1.5">
            {TOKEN_SCOPES.map((scope) => (
              <label key={scope.value} className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={scopes.includes(scope.value)}
                  onChange={() => toggleScope(scope.value)}
                />
                <span>
                  <code className="inline">{scope.text}</code> <span className="text-muted">{scope.description}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        {error ? (
          <Notice tone="red" role="alert">
            {error}
          </Notice>
        ) : null}
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Creating…" : "Create token"}
        </button>
      </form>
    </Section>
  );
}
