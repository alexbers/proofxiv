import Link from "next/link";

import type { Schemas } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { boundSign, changeText, fmtValue } from "@/lib/records";

import { Markdown } from "../Markdown";
import { Chip, ExternalLink } from "../ui";

type Row = Schemas["RecordBoardOut"];

function Status({ row }: { row: Row }) {
  const since = row.still_best ? null : (
    <Chip tone="gray" description="A better value has been recorded since.">
      since improved
    </Chip>
  );
  let main;
  if (row.kind === "platform" && row.paper_id) {
    main = (
      <Link href={`/papers/${row.paper_id}`} className="whitespace-nowrap">
        <span aria-hidden="true">✓ </span>verified on ProofXiv
      </Link>
    );
  } else if (row.kind === "literature") {
    main = row.source_url ? (
      <ExternalLink href={row.source_url} className="whitespace-nowrap">
        <span aria-hidden="true">📚 </span>found in the literature
      </ExternalLink>
    ) : (
      <span className="whitespace-nowrap">
        <span aria-hidden="true">📚 </span>found in the literature
      </span>
    );
  } else if (row.paper_id && row.kind === "machine_checked_unverified") {
    main = (
      <Link
        href={`/papers/${row.paper_id}`}
        title="The record is machine-checked, but the platform's review did not verify the paper; its labels say why."
      >
        <span aria-hidden="true">⚙ </span>machine-checked, not verified by the platform
      </Link>
    );
  } else if (row.paper_id) {
    main = (
      <Link
        href={`/papers/${row.paper_id}`}
        title="The record is machine-checked; the platform's review of the paper is still pending."
      >
        <span aria-hidden="true">⚙ </span>machine-checked, awaiting the platform
      </Link>
    );
  } else {
    main = <span>{row.kind}</span>;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {main}
      {since}
    </span>
  );
}

function Step({ row }: { row: Row }) {
  return (
    <span className="font-mono tabular-nums">
      {row.previous ? (
        <>
          <span className="text-muted">{fmtValue(row.previous)}</span>
          <span aria-hidden="true" className="mx-1 text-muted">
            →
          </span>
          <span className="sr-only"> to </span>
        </>
      ) : null}
      <strong>{fmtValue(row.value)}</strong>
    </span>
  );
}

function Change({ row }: { row: Row }) {
  const text = changeText(row.bound, row.previous, row.value, row.gain);
  if (!text) return <span className="text-muted">—</span>;
  return <span className="font-mono text-sm whitespace-nowrap tabular-nums">{text}</span>;
}

function ProblemLink({ row, className }: { row: Row; className?: string }) {
  return (
    <Link href={`/problems/${row.problem_slug}`} className={className}>
      <Markdown inline noLinks>
        {row.problem_title}
      </Markdown>
    </Link>
  );
}

function MoreLink({ slug }: { slug: string }) {
  return <Link href={`/problems/${slug}`}>More new records on this problem →</Link>;
}

/** At most this many rows per problem; the rest is behind a link to the problem's record table. */
const PER_PROBLEM = 2;

type Item = { key: string; row: Row } | { key: string; moreOf: string };

/** One paper can improve dozens of bounds of one problem at once: keep the board varied. */
function collapse(rows: Row[]): Item[] {
  const total = new Map<string, number>();
  for (const row of rows) total.set(row.problem_slug, (total.get(row.problem_slug) ?? 0) + 1);
  const shown = new Map<string, number>();
  const items: Item[] = [];
  rows.forEach((row, i) => {
    const n = (shown.get(row.problem_slug) ?? 0) + 1;
    if (n > PER_PROBLEM) return;
    shown.set(row.problem_slug, n);
    items.push({ key: `row-${i}`, row });
    if (n === PER_PROBLEM && (total.get(row.problem_slug) ?? 0) > PER_PROBLEM) {
      items.push({ key: `more-${i}`, moreOf: row.problem_slug });
    }
  });
  return items;
}

function TableRow({ row }: { row: Row }) {
  return (
    <tr>
      <td className="min-w-52">
        <ProblemLink row={row} />
      </td>
      <td className="whitespace-nowrap">
        <Markdown inline noLinks>{row.quantity}</Markdown> <span className="text-muted">{boundSign(row.bound)}</span>
      </td>
      <td className="whitespace-nowrap">
        <Step row={row} />
      </td>
      <td>
        <Change row={row} />
      </td>
      <td className="min-w-72">
        <Status row={row} />
      </td>
      <td className="whitespace-nowrap text-muted">{fmtDate(row.at)}</td>
    </tr>
  );
}

function ListRow({ row }: { row: Row }) {
  return (
    <li className="space-y-1 px-4 py-3 text-sm">
      <ProblemLink row={row} className="font-medium wrap-anywhere" />
      <p className="flex flex-wrap items-baseline gap-x-2">
        <span>
          <Markdown inline noLinks>{row.quantity}</Markdown> {boundSign(row.bound)}
        </span>
        <Step row={row} />
        <Change row={row} />
      </p>
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted">
        <Status row={row} />
        <span>{fmtDate(row.at)}</span>
      </p>
    </li>
  );
}

export function RecordBoard({ rows }: { rows: Row[] }) {
  const items = collapse(rows);
  return (
    <>
      {/* desktop: a table */}
      <div className="card table-scroll hidden md:block">
        <table className="data-table">
          <caption className="sr-only">Records broken lately, newest first</caption>
          <thead>
            <tr>
              <th scope="col">Problem</th>
              <th scope="col">Quantity</th>
              <th scope="col" className="whitespace-nowrap">Previous → new</th>
              <th scope="col">Change</th>
              <th scope="col">Status</th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) =>
              "row" in item ? (
                <TableRow key={item.key} row={item.row} />
              ) : (
                <tr key={item.key}>
                  <td colSpan={6} className="text-sm">
                    <MoreLink slug={item.moreOf} />
                  </td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>

      {/* phone: a stacked list */}
      <ul className="card divide-y divide-line md:hidden">
        {items.map((item) =>
          "row" in item ? (
            <ListRow key={item.key} row={item.row} />
          ) : (
            <li key={item.key} className="px-4 py-2.5 text-sm">
              <MoreLink slug={item.moreOf} />
            </li>
          ),
        )}
      </ul>
    </>
  );
}
