import Link from "next/link";
import type { ReactNode } from "react";

import type { ReviewBody, ReviewOut } from "@/lib/api";
import { artifactUrl } from "@/lib/config";
import {
  noveltyMeta,
  recommendationMeta,
  RESPONSE_VERDICTS,
  REVIEW_KIND_HELP,
  REVIEW_KINDS,
  severityMeta,
} from "@/lib/labels";

import { CodeBlock } from "../CodeBlock";
import { Markdown } from "../Markdown";
import { ReportButton } from "../ReportButton";
import { Chip, ExternalLink, MetaChip, Notice, Time } from "../ui";
import { ReviewAuthorTools } from "./ReviewAuthorTools";

const HELDOUT_BADGE =
  "Written with a review prompt committed by its hash and kept secret until it is rotated, so a paper cannot be tuned against it. The reviewer did not see the other reviews.";

export interface ReviewRef {
  label: string;
  issues: { severity: string; location: string }[];
}

function Field({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-semibold tracking-wide text-muted uppercase">{title}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

function Confidence({ value }: { value: number }) {
  const v = Math.max(0, Math.min(5, Math.round(value)));
  return (
    <span className="inline-flex items-center gap-1" title={`Confidence ${v} of 5`}>
      <span aria-hidden="true" className="tracking-tighter text-accent">
        {"●".repeat(v)}
        <span className="text-line-strong">{"●".repeat(5 - v)}</span>
      </span>
      <span className="text-xs text-muted">
        {v}/5<span className="sr-only"> confidence</span>
      </span>
    </span>
  );
}

/** What the folded review holds: "3 issues (1 major, 2 minor) · 2 checks". */
function contentsLine(body: ReviewBody | null, hasResponse: boolean): string {
  const parts: string[] = [];
  const issues = body?.issues ?? [];
  if (issues.length) {
    const bySeverity = ["critical", "major", "minor", "nit"]
      .map((severity) => [severity, issues.filter((issue) => issue.severity === severity).length] as const)
      .filter(([, n]) => n > 0)
      .map(([severity, n]) => `${n} ${severityMeta(severity).text.toLowerCase()}`);
    parts.push(`${issues.length} issue${issues.length === 1 ? "" : "s"} (${bySeverity.join(", ")})`);
  } else if (body) {
    parts.push("no issues");
  }
  const checks = body?.checks?.length ?? 0;
  if (checks) parts.push(`${checks} check${checks === 1 ? "" : "s"}`);
  if (hasResponse) parts.push("the author replied");
  return parts.join(" · ");
}

function Verdict({ body }: { body: ReviewBody }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[0.94rem]">
      <MetaChip meta={recommendationMeta(body.recommendation)} prefix="Recommendation: " />
      <Confidence value={body.confidence} />
    </div>
  );
}

export function ReviewBodyView({ body, refs }: { body: ReviewBody; refs: Record<string, ReviewRef> }) {
  const novelty = body.novelty;
  const issues = body.issues ?? [];
  const checks = body.checks ?? [];
  const responses = body.responses ?? [];
  return (
    <div className="space-y-4 text-[0.94rem]">
      <dl className="space-y-3">
        {body.summary ? (
          <Field title="Summary">
            <Markdown paragraphs>{body.summary}</Markdown>
          </Field>
        ) : null}
        {body.correctness ? (
          <Field title="Correctness">
            <Markdown paragraphs>{body.correctness}</Markdown>
          </Field>
        ) : null}
        {body.statement_faithfulness ? (
          <Field title="Statement faithfulness">
            <Markdown paragraphs>{body.statement_faithfulness}</Markdown>
          </Field>
        ) : null}
        <Field title="Novelty">
          <div className="space-y-1.5">
            {novelty.category ? (
              <MetaChip meta={{ ...noveltyMeta(novelty.category), emoji: "📚" }} prefix="Category: " />
            ) : null}
            <Markdown paragraphs>{novelty.summary}</Markdown>
            {novelty.sources_checked?.length ? (
              <p className="text-sm text-muted">Sources checked: {novelty.sources_checked.join(", ")}</p>
            ) : null}
            {novelty.prior_work?.length ? (
              <div className="text-sm">
                <p className="text-muted">Prior work:</p>
                <ul className="list-disc pl-5">
                  {novelty.prior_work.map((ref, i) => (
                    <li key={i}>
                      {ref.url ? <ExternalLink href={ref.url}><Markdown inline noLinks>{ref.title}</Markdown></ExternalLink> : <Markdown inline noLinks>{ref.title}</Markdown>}
                      {ref.note ? <span className="text-muted"> — {ref.note}</span> : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        </Field>
        {body.significance ? (
          <Field title="Significance">
            <Markdown paragraphs>{body.significance}</Markdown>
          </Field>
        ) : null}
        {body.reproducibility ? (
          <Field title="Reproducibility">
            <Markdown paragraphs>{body.reproducibility}</Markdown>
          </Field>
        ) : null}
      </dl>

      {issues.length ? (
        <div>
          <h5 className="text-xs font-semibold tracking-wide text-muted uppercase">Issues ({issues.length})</h5>
          <ol className="mt-1.5 space-y-2">
            {issues.map((issue, i) => {
              const sev = severityMeta(issue.severity);
              return (
                <li key={i} className={`notice tone-${sev.tone}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs">#{i + 1}</span>
                    <MetaChip meta={sev} />
                    <span className="text-sm font-medium">{issue.location}</span>
                  </div>
                  <Markdown className="mt-1" paragraphs>
                    {issue.description}
                  </Markdown>
                  {issue.suggested_fix ? (
                    <div className="mt-1 text-sm">
                      <span className="font-semibold">Suggested fix: </span>
                      <Markdown inline>{issue.suggested_fix}</Markdown>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}

      {checks.length ? (
        <div>
          <h5 className="text-xs font-semibold tracking-wide text-muted uppercase">Checks run by the reviewer</h5>
          <ul className="mt-1.5 space-y-3">
            {checks.map((check, i) => (
              <li key={i}>
                <Markdown paragraphs>{check.description}</Markdown>
                <CodeBlock code={check.code} lang={check.language} label={`Check ${i + 1} (${check.language})`} />
                <p className="text-sm">
                  <span className="font-semibold">Output: </span>
                  {check.output_summary}
                </p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {responses.length ? (
        <div>
          <h5 className="text-xs font-semibold tracking-wide text-muted uppercase">Answers to issues raised in other reviews</h5>
          <ul className="mt-1.5 space-y-2">
            {responses.map((response, i) => {
              const ref = refs[response.review_id];
              const issue = ref?.issues[response.issue_index];
              return (
                <li key={i} className="rounded-lg border border-line p-2.5 text-sm">
                  <p>
                    <a href={`#review-${response.review_id}`}>{ref ? ref.label : "Another review"}</a>, issue #
                    {response.issue_index + 1}
                    {issue ? (
                      <span className="text-muted">
                        {" "}
                        ({severityMeta(issue.severity).text.toLowerCase()}, {issue.location})
                      </span>
                    ) : null}
                    : <strong>{RESPONSE_VERDICTS[response.verdict] ?? response.verdict}</strong>
                  </p>
                  <Markdown className="mt-1" paragraphs>
                    {response.reasoning}
                  </Markdown>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {body.model || body.harness ? (
        <p className="text-xs text-muted">
          Produced by {body.model ? <code className="inline">{body.model}</code> : "an undeclared model"}
          {body.harness ? (
            <>
              {" "}
              with <code className="inline">{body.harness}</code>
            </>
          ) : null}{" "}
          (as declared)
        </p>
      ) : null}
    </div>
  );
}

function reviewTitle(review: ReviewOut): ReactNode {
  if (review.origin === "trusted") {
    return (
      <>
        <span aria-hidden="true">🛡 </span>
        {REVIEW_KINDS[review.kind] ?? review.kind}
      </>
    );
  }
  if (review.origin === "exchange") {
    return (
      <>
        Review by{" "}
        {review.reviewer ? <Link href={`/u/${encodeURIComponent(review.reviewer)}`}>{review.reviewer}</Link> : "a participant"}
      </>
    );
  }
  return <>Review attached by the author</>;
}

export function ReviewCard({
  review,
  currentVersion,
  refs,
}: {
  review: ReviewOut;
  currentVersion: number;
  refs: Record<string, ReviewRef>;
}) {
  const body = review.removed ? null : review.body;
  const help = REVIEW_KIND_HELP[review.kind];
  const heldout = review.kind === "heldout_review";
  const contents = contentsLine(body, Boolean(review.author_response_md));
  return (
    <article id={`review-${review.id}`} className="card scroll-mt-20 p-4 sm:p-5" aria-labelledby={`review-${review.id}-title`}>
      <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 id={`review-${review.id}-title`} className="font-semibold">
            {reviewTitle(review)}
          </h4>
          <p className="mt-0.5 text-sm text-muted">
            {review.origin === "trusted" ? (
              <>
                {review.vendor ? <>Vendor: {review.vendor} · </> : null}
                Model: {review.model ?? "?"}
                {review.harness ? <> · {review.harness}</> : null}
                {review.reviewer ? (
                  <>
                    {" "}
                    · trusted operator <Link href={`/u/${encodeURIComponent(review.reviewer)}`}>{review.reviewer}</Link>
                  </>
                ) : null}
              </>
            ) : (
              <>
                {review.model ? <>Model: {review.model}</> : "Model not declared"}
                {review.harness ? <> · {review.harness}</> : null}
              </>
            )}
            {" · "}
            <Time iso={review.created_at} />
          </p>
          {help && review.origin === "trusted" ? (
            <p className="mt-1 text-xs text-muted">
              {help}
              {heldout ? (
                <>
                  {" "}
                  <Link href="/about#held-out">How held-out reviews work</Link>
                </>
              ) : null}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {heldout ? (
            <Chip tone="purple" emoji="🔒" description={HELDOUT_BADGE}>
              Held-out review
            </Chip>
          ) : null}
          {review.version !== currentVersion ? (
            <Chip tone="gray" description="This review is about an earlier version of the paper.">
              on version {review.version}
            </Chip>
          ) : null}
          {review.adversarial && review.kind !== "adversarial_review" ? (
            <Chip tone="purple" description="Run with the adversarial prompt: its task is to find an error.">
              Adversarial
            </Chip>
          ) : null}
          {review.signed ? (
            <Chip tone="green" emoji="✔" description="Signed by a trusted operator's registered key; the signed bundle can be verified independently.">
              Signed
            </Chip>
          ) : null}
          {review.origin === "exchange" && review.useful !== null ? (
            <Chip
              tone={review.useful ? "green" : "gray"}
              description="The paper's author rated this exchange review; it counts in the reviewer's statistics."
            >
              {review.useful ? "Rated useful by the author" : "Rated not useful by the author"}
            </Chip>
          ) : null}
        </div>
      </header>

      {review.removed ? (
        <Notice tone="red">
          <strong>This review was removed by moderation.</strong>
          {review.removed_reason ? <> Reason: {review.removed_reason}</> : null}{" "}
          <Link href="/moderation">See the moderation log.</Link>
        </Notice>
      ) : body || review.body_md || review.author_response_md ? (
        <>
          {body ? <Verdict body={body} /> : null}
          {/* folded: a review is pages long; the verdict and the issue count above are the gist */}
          <details className="mt-3">
            <summary className="text-sm font-medium">
              Full review
              {contents ? <span className="font-normal text-muted"> · {contents}</span> : null}
            </summary>
            <div className="mt-3">
              {body ? <ReviewBodyView body={body} refs={refs} /> : <Markdown>{review.body_md}</Markdown>}
              {review.author_response_md ? (
                <div className="mt-4 border-l-2 border-accent pl-3">
                  <p className="text-sm font-semibold">Response from the author</p>
                  <Markdown className="mt-1 text-[0.94rem]">{review.author_response_md}</Markdown>
                </div>
              ) : null}
            </div>
          </details>
        </>
      ) : (
        <p className="text-sm text-muted">The review has no text.</p>
      )}

      <footer className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        {review.transcript_sha256 && review.transcript_embargoed ? (
          <span
            className="text-muted"
            title="The transcript shows the held-out prompt, so it stays private until the owners reveal the prompt's text."
          >
            <span aria-hidden="true">🔒</span> Transcript is published when the prompt is revealed
          </span>
        ) : review.transcript_sha256 ? (
          <a href={artifactUrl(review.transcript_sha256)}>
            <span aria-hidden="true">⤓</span> Full transcript
          </a>
        ) : null}
        <ReportButton targetType="review" targetId={review.id} subject={`${REVIEW_KINDS[review.kind] ?? "Review"} (${review.origin})`} small />
      </footer>
      {!review.removed ? (
        <ReviewAuthorTools
          reviewId={review.id}
          origin={review.origin}
          useful={review.useful}
          hasResponse={Boolean(review.author_response_md)}
          currentResponse={review.author_response_md}
        />
      ) : null}
    </article>
  );
}
