import Link from "next/link";

import type { ClaimOut, RunOut, Schemas } from "@/lib/api";
import { artifactUrl } from "@/lib/config";
import {
  BOUNDS,
  CLAIM_KINDS,
  JOB_STATUSES,
  labelMeta,
  NATIVE_AXIOM_MARKERS,
  STANDARD_AXIOMS,
  verdictMeta,
} from "@/lib/labels";

import { CodeBlock } from "../CodeBlock";
import { Markdown } from "../Markdown";
import { Chip, ExternalLink, Hash, LevelBadge, MetaChip, Time } from "../ui";

/** A claim as declared in the submission manifest (paper.json). */
export type ClaimSpec = Schemas["ClaimSpec"];

function fmtDelta(delta: number | null | undefined): string {
  if (delta == null || Number.isNaN(delta)) return "";
  const rounded = Math.abs(delta) >= 1 ? Math.round(delta * 1000) / 1000 : Number(delta.toPrecision(4));
  return `${rounded > 0 ? "+" : rounded < 0 ? "−" : "±"}${Math.abs(rounded)}`;
}

function RecordLine({ claim }: { claim: ClaimOut }) {
  const status = claim.record_status;
  if (!status) return null;
  const source = status.source ? (
    <>
      {" "}
      — source: <ExternalLink href={status.source}>{status.source}</ExternalLink>
    </>
  ) : null;
  if (status.status === "new_record") {
    return (
      <span>
        <Chip tone="green" emoji="🏆" description="Beats the best value recorded in the problem's record table.">
          New record
        </Chip>{" "}
        <strong>{claim.value}</strong> (was {status.previous}
        {status.delta != null ? `, ${fmtDelta(status.delta)}` : ""}){source}
      </span>
    );
  }
  if (status.status === "not_better") {
    return (
      <span>
        <Chip tone="amber" description="The value does not improve the recorded best.">
          Not a new record
        </Chip>{" "}
        {claim.value} does not beat the recorded {status.previous}
        {source}
      </span>
    );
  }
  if (status.status === "no_previous") return <span>No previous record for this quantity.</span>;
  return <span>{status.status}</span>;
}

function Axioms({ axioms }: { axioms: string[] }) {
  if (!axioms.length) return <span className="text-muted">no axioms</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {axioms.map((axiom) => {
        const native = NATIVE_AXIOM_MARKERS.some((m) => axiom.includes(m));
        if (STANDARD_AXIOMS.has(axiom)) {
          return (
            <code key={axiom} className="inline" title="Standard axiom">
              {axiom}
            </code>
          );
        }
        return (
          <Chip
            key={axiom}
            tone={native ? "amber" : "red"}
            square
            className="font-mono"
            description={native ? "Trusts the compiler (native_decide)." : "Non-standard axiom."}
          >
            {axiom}
          </Chip>
        );
      })}
    </span>
  );
}

function RunItem({ run }: { run: RunOut }) {
  const { lean, checker } = run;
  const verdict = verdictMeta(run.verdict);
  // the signed payload is the runner's RunResult (its shape is versioned by its "type")
  const payload = run.bundle.payload;
  const keyFingerprint = run.bundle.key_fingerprint;
  return (
    <li className="rounded-lg border border-line p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <MetaChip meta={verdict} />
        {run.accepted ? (
          <Chip tone="green" description="The server accepted the signed result as a machine check of this claim.">
            ✓ Accepted
          </Chip>
        ) : (
          <Chip tone="red" description="The server did not accept this run as a check of the claim.">
            ✗ Not accepted
          </Chip>
        )}
        {run.native_decide ? <MetaChip meta={labelMeta("native_decide")} /> : null}
        {run.invalidated ? (
          <Chip tone="amber" emoji="↻" description="The signing key was revoked; this run no longer counts.">
            Signature revoked
          </Chip>
        ) : null}
        <span className="text-muted">
          by{" "}
          {run.runner ? (
            <Link href={`/u/${encodeURIComponent(run.runner)}`}>{run.runner}</Link>
          ) : (
            "unknown runner"
          )}
          , <Time iso={run.created_at} withTime />
        </span>
      </div>
      <dl className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-[9rem_minmax(0,1fr)]">
        {run.evaluation ? (
          <>
            <dt className="text-muted">Evaluation</dt>
            <dd>{run.evaluation === "ok" ? "ok — all requirements met" : run.evaluation}</dd>
          </>
        ) : null}
        {lean?.theorems && Object.keys(lean.theorems).length ? (
          <>
            <dt className="text-muted">Axioms per theorem</dt>
            <dd>
              <ul className="space-y-1">
                {Object.entries(lean.theorems).map(([theorem, axioms]) => (
                  <li key={theorem} className="flex flex-wrap items-baseline gap-1.5">
                    <code className="inline">{theorem}</code>
                    <span aria-hidden="true">:</span>
                    <Axioms axioms={axioms} />
                  </li>
                ))}
              </ul>
            </dd>
          </>
        ) : null}
        {lean?.has_sorry ? (
          <>
            <dt className="text-muted">Lean</dt>
            <dd className="text-red-700 dark:text-red-300">The build reported `sorry`.</dd>
          </>
        ) : null}
        {lean?.errors?.length ? (
          <>
            <dt className="text-muted">Lean errors</dt>
            <dd>
              <ul className="list-disc pl-5 font-mono text-xs">
                {lean.errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </dd>
          </>
        ) : null}
        {checker ? (
          <>
            <dt className="text-muted">Checker</dt>
            <dd>
              <code className="inline">{checker.checker}</code>{" "}
              <span className="inline-flex flex-wrap gap-1 align-middle">
                {Object.entries(checker.implementations ?? {}).map(([impl, ok]) => (
                  <Chip key={impl} tone={ok ? "green" : "red"} square description={ok ? "This implementation accepted the certificate." : "This implementation rejected the certificate."}>
                    {impl} {ok ? "✓" : "✗"}
                  </Chip>
                ))}
              </span>
              {checker.details ? <span className="mt-1 block text-muted">{checker.details}</span> : null}
            </dd>
          </>
        ) : null}
        {typeof payload.runner === "string" ? (
          <>
            <dt className="text-muted">Runner software</dt>
            <dd>{payload.runner}</dd>
          </>
        ) : null}
        {typeof payload.duration_s === "number" || typeof payload.peak_memory_mb === "number" ? (
          <>
            <dt className="text-muted">Resources</dt>
            <dd>
              {typeof payload.duration_s === "number" ? `${Math.round(payload.duration_s)} s` : ""}
              {typeof payload.duration_s === "number" && typeof payload.peak_memory_mb === "number" ? " · " : ""}
              {typeof payload.peak_memory_mb === "number" ? `peak ${payload.peak_memory_mb} MB` : ""}
            </dd>
          </>
        ) : null}
      </dl>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {run.log_sha256 ? <a href={artifactUrl(run.log_sha256)}>Download log</a> : null}
        {keyFingerprint ? (
          <span className="text-muted">
            Signed with key <Hash value={keyFingerprint} length={16} />
          </span>
        ) : null}
        <details className="w-full">
          <summary className="text-muted">Signed bundle (JSON)</summary>
          <pre className="mt-1 max-h-72 overflow-auto rounded-md border border-line bg-surface-2 p-2 font-mono text-[0.72rem] leading-snug">
            {JSON.stringify(run.bundle, null, 2)}
          </pre>
        </details>
      </div>
    </li>
  );
}

function machineCheckText(claim: ClaimOut): string {
  if (claim.kind === "informal") return "Informal claim: not machine-checkable (reviewed by the platform's anchor reviews).";
  if (claim.c1_issues.length) return "Not run: the server checks found blocking issues.";
  const valid = claim.runs.filter((r) => r.accepted && !r.invalidated);
  if (valid.length) return "Passed a trusted run.";
  if (claim.runs.some((r) => r.invalidated)) return "Needs re-checking: the signing key of the earlier run was revoked.";
  if (claim.job_status === "done" && claim.runs.length) return "Failed: the trusted run did not accept the claim (see below).";
  if (claim.job_status) return `Trusted run ${JOB_STATUSES[claim.job_status] ?? claim.job_status}.`;
  return "No machine check scheduled.";
}

export function ClaimCard({
  claim,
  spec,
  files,
  leanSource,
  problemTitles,
}: {
  claim: ClaimOut;
  spec?: ClaimSpec;
  files: Record<string, string>;
  leanSource?: string | null;
  problemTitles?: Record<string, string>;
}) {
  const leanFile = spec?.lean_file ?? null;
  const certFile = spec?.certificate_file ?? null;
  return (
    <article id={`claim-${claim.key}`} className="card scroll-mt-20 p-4" aria-labelledby={`claim-${claim.key}-title`}>
      <header className="flex flex-wrap items-center gap-2">
        <h3 id={`claim-${claim.key}-title`} className="font-mono text-sm font-semibold">
          {claim.key}
        </h3>
        <Chip tone={claim.kind === "informal" ? "purple" : "slate"} square>
          {CLAIM_KINDS[claim.kind] ?? claim.kind}
        </Chip>
        <LevelBadge level={claim.level} />
        {claim.labels.map((label) => (
          <MetaChip key={label} meta={labelMeta(label)} />
        ))}
      </header>
      <Markdown className="mt-2">{claim.statement}</Markdown>

      <dl className="mt-3 grid gap-x-4 gap-y-1.5 text-sm sm:grid-cols-[9rem_minmax(0,1fr)]">
        {claim.value ? (
          <>
            <dt className="text-muted">Value</dt>
            <dd>
              {BOUNDS[claim.bound ?? ""] ?? claim.bound}: <strong>{claim.value}</strong>
              {spec?.quantity ? (
                <>
                  {" "}
                  for <code className="inline">{spec.quantity}</code>
                </>
              ) : null}
              {spec?.problem_slug ? (
                <>
                  {" "}
                  of <Link href={`/problems/${spec.problem_slug}`}>{problemTitles?.[spec.problem_slug] ?? spec.problem_slug}</Link>
                </>
              ) : null}
            </dd>
          </>
        ) : null}
        {claim.record_status ? (
          <>
            <dt className="text-muted">Record status</dt>
            <dd>
              <RecordLine claim={claim} />
            </dd>
          </>
        ) : null}
        {claim.lean_theorems.length ? (
          <>
            <dt className="text-muted">Lean theorems</dt>
            <dd className="flex flex-wrap items-center gap-1">
              {claim.lean_theorems.map((t) => (
                <code key={t} className="inline">
                  {t}
                </code>
              ))}
              {leanFile ? (
                <span className="text-muted">
                  in{" "}
                  {files[leanFile] ? <a href={artifactUrl(files[leanFile])}>{leanFile}</a> : <code className="inline">{leanFile}</code>}
                </span>
              ) : null}
            </dd>
          </>
        ) : null}
        {claim.certificate_type ? (
          <>
            <dt className="text-muted">Certificate</dt>
            <dd>
              <code className="inline">{claim.certificate_type}</code>
              {certFile ? (
                <span className="text-muted">
                  {" "}
                  in {files[certFile] ? <a href={artifactUrl(files[certFile])}>{certFile}</a> : certFile}
                </span>
              ) : null}
            </dd>
          </>
        ) : null}
        {spec?.depends_on?.length ? (
          <>
            <dt className="text-muted">Depends on</dt>
            <dd className="flex flex-wrap gap-1">
              {spec.depends_on.map((k) => (
                <a key={k} href={`#claim-${k}`} className="font-mono text-sm">
                  {k}
                </a>
              ))}
            </dd>
          </>
        ) : null}
        <dt className="text-muted">Machine check</dt>
        <dd>{machineCheckText(claim)}</dd>
      </dl>

      {claim.c1_issues.length ? (
        <div className="notice tone-red mt-3" role="note">
          <p className="font-semibold">Blocking issues found by the server (C1 checks)</p>
          <ul className="mt-1 list-disc pl-5">
            {claim.c1_issues.map((issue, i) => (
              <li key={i}>
                <Markdown inline>{issue}</Markdown>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {claim.runs.length ? (
        <div className="mt-3">
          <h4 className="mb-1.5 text-sm font-semibold">
            Signed runs <span className="font-normal text-muted">({claim.runs.length})</span>
          </h4>
          <ul className="space-y-2">
            {[...claim.runs].reverse().map((run) => (
              <RunItem key={run.id} run={run} />
            ))}
          </ul>
        </div>
      ) : null}

      {leanSource ? (
        <details className="mt-3">
          <summary className="text-sm font-medium">Lean source: {leanFile}</summary>
          <CodeBlock code={leanSource} lang="lean4" label={`Lean source ${leanFile}`} />
        </details>
      ) : null}
    </article>
  );
}
