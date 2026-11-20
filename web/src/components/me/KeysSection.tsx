"use client";

import { useId, useState, type FormEvent } from "react";

import { api, attempt } from "@/lib/api";

import { Chip, Empty, Hash, Notice, Section, Time } from "../ui";
import { useApi } from "../useApi";

export function KeysSection({ meId }: { meId: string }) {
  const keys = useApi(`keys:${meId}`, () => attempt(() => api.GET("/api/v1/me/keys")));
  const formId = useId();
  const [publicKey, setPublicKey] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "green" | "red"; text: string } | null>(null);

  const add = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    const result = await attempt(() =>
      api.POST("/api/v1/me/keys", { body: { public_key: publicKey.trim(), label: label.trim() } }),
    );
    setBusy(false);
    if (result.ok) {
      setMessage({ tone: "green", text: `Key ${result.data.fingerprint} registered.` });
      setPublicKey("");
      setLabel("");
      keys.reload();
    } else {
      setMessage({ tone: "red", text: result.message });
    }
  };

  const list = keys.data ?? [];
  return (
    <Section
      id="keys"
      title="Signing keys"
      count={list.length}
      description="Ed25519 public keys of your proofxiv. Runners and trusted operators sign their results with them; only the owners can revoke a key (with a signed statement)."
    >
      {keys.error ? (
        <Notice tone="red" role="alert">
          {keys.error}
        </Notice>
      ) : keys.loading ? (
        <p className="text-sm text-muted">Loading keys…</p>
      ) : list.length ? (
        <ul className="card divide-y divide-line">
          {list.map((key) => (
            <li key={key.fingerprint} className="space-y-1 px-4 py-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Hash value={key.fingerprint} length={32} />
                {key.label ? <span className="font-medium">{key.label}</span> : null}
                {key.revoked_at ? (
                  <Chip tone="red">
                    revoked <Time iso={key.revoked_at} />
                  </Chip>
                ) : (
                  <Chip tone="green">active</Chip>
                )}
                <span className="text-muted">
                  added <Time iso={key.created_at} />
                </span>
              </div>
              <p className="font-mono text-xs break-all text-muted">{key.public_key}</p>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>No signing keys. You need one only to run machine checks or trusted tasks.</Empty>
      )}

      <form onSubmit={add} className="card mt-4 space-y-3 p-4" aria-labelledby={`${formId}-title`}>
        <h3 id={`${formId}-title`} className="font-semibold">
          Register a key
        </h3>
        <p className="hint">
          Usually you do not need this form: <code className="inline">proofxiv keys new</code> creates a key and registers it.
        </p>
        <div className="grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div>
            <label htmlFor={`${formId}-key`} className="label">
              Public key (base64)
            </label>
            <input
              id={`${formId}-key`}
              className="field font-mono text-sm"
              required
              value={publicKey}
              onChange={(e) => setPublicKey(e.target.value)}
              placeholder="DCQkkG3cqn8Pc6n/KD2hF4pc3bL1EWi+GCCJ6r+bE60="
              spellCheck={false}
            />
          </div>
          <div>
            <label htmlFor={`${formId}-label`} className="label">
              Label <span className="font-normal text-muted">(optional)</span>
            </label>
            <input id={`${formId}-label`} className="field" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="runner on gpu-box" />
          </div>
        </div>
        {message ? (
          <Notice tone={message.tone} role={message.tone === "red" ? "alert" : "status"}>
            {message.text}
          </Notice>
        ) : null}
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Registering…" : "Register key"}
        </button>
      </form>
    </Section>
  );
}
