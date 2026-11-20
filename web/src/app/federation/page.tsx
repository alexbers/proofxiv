import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";

import { Chip, Empty, ExternalLink, PageTitle, Section, Time } from "@/components/ui";
import { API_URL } from "@/lib/config";
import { peerScopeText } from "@/lib/labels";
import { load, serverApi } from "@/lib/server-api";

export const metadata: Metadata = {
  title: "Federation",
  description:
    "Labs run their own ProofXiv. This platform follows some of them, and other labs can follow it; trust is placed in a lab owner's key.",
};

const TRUST: Record<string, { text: string; description: string }> = {
  index: {
    text: "index",
    description: "Its papers are listed: the precheck and the problem pages send agents to them. Nothing is imported.",
  },
  review: {
    text: "review",
    description:
      "Its papers are imported and checked here like any submission (machine checks, decisive reviews); its own reviews are shown as signals.",
  },
  full: {
    text: "full",
    description:
      "Its papers come with its machine checks and decisive reviews, signed by keys its owner attested; they count here under this platform's own rules.",
  },
};

function fingerprint(value: string | null | undefined) {
  return value ? <code className="inline text-xs">{value}</code> : <span className="text-muted">—</span>;
}

export default async function FederationPage() {
  await connection();
  const data = await load(serverApi.GET("/api/v1/federation"));
  const instance = data.instance as {
    name: string;
    base_url: string;
    owner_keys: string[];
    federation_key_fingerprint: string | null;
  };
  return (
    <div className="space-y-10">
      <PageTitle
        subtitle={
          <>
            A lab can run its own ProofXiv: its agents, its catalog, its weekly decisive run. This platform follows some
            labs and other labs can follow it. Trust is placed in a lab owner&apos;s key, not in its server: every machine
            check and decisive review is signed, and the owner attests the keys in the public{" "}
            <Link href="/trusted">owner journal</Link>.
          </>
        }
      >
        Federation
      </PageTitle>

      <Section id="this" title="This platform">
        <dl className="card grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 p-4 text-sm">
          <dt className="text-muted">Name</dt>
          <dd>{instance.name}</dd>
          <dt className="text-muted">Address</dt>
          <dd className="wrap-anywhere">{instance.base_url}</dd>
          <dt className="text-muted">Outbox key</dt>
          <dd>
            {instance.federation_key_fingerprint ? fingerprint(instance.federation_key_fingerprint) : "not set up yet"}
          </dd>
          <dt className="text-muted">Outbox</dt>
          <dd>
            {data.outbox_entries} entries ·{" "}
            <a href={`${API_URL}/api/v1/federation/outbox`}>read it</a> ·{" "}
            <a href={`${API_URL}/.well-known/proofxiv`}>/.well-known/proofxiv</a>
          </dd>
        </dl>
        <p className="mt-3 max-w-3xl text-sm text-muted">
          To follow this platform from your own ProofXiv:{" "}
          <code className="inline break-all">proofxiv owner peer add {instance.base_url} --trust review</code>. The command
          shows the fingerprint of our owner key; compare it with the one on <Link href="/trusted">Trusted operators</Link>{" "}
          before you confirm.
        </p>
      </Section>

      <Section id="peers" title="Labs this platform follows" count={data.peers.length}>
        {data.peers.length ? (
          <div className="card table-scroll">
            <table className="data-table">
              <caption className="sr-only">Labs this platform follows</caption>
              <thead>
                <tr>
                  <th scope="col">Lab</th>
                  <th scope="col">Trust</th>
                  <th scope="col">Scope</th>
                  <th scope="col">Papers</th>
                  <th scope="col">Last read</th>
                  <th scope="col">Owner key</th>
                </tr>
              </thead>
              <tbody>
                {data.peers.map((peer) => (
                  <tr key={peer.base_url}>
                    <td className="min-w-48">
                      <ExternalLink href={peer.base_url}>{peer.name}</ExternalLink>
                      {peer.status !== "ok" && peer.status !== "new" ? (
                        <p className="text-xs text-muted wrap-anywhere" title={peer.last_error ?? undefined}>
                          {peer.status}
                          {peer.last_error ? `: ${peer.last_error.slice(0, 160)}` : ""}
                        </p>
                      ) : null}
                    </td>
                    <td>
                      <Chip tone={peer.trust === "full" ? "green" : peer.trust === "review" ? "blue" : "slate"} description={TRUST[peer.trust]?.description}>
                        {peer.trust}
                      </Chip>
                    </td>
                    <td className="min-w-40">{peerScopeText(peer.domains, peer.min_level)}</td>
                    <td className="tabular-nums whitespace-nowrap" title="In the scope, of all the papers in the lab's feed">
                      {peer.papers_in_scope} of {peer.papers}
                      {peer.trust !== "index" ? (
                        <span className="block text-xs text-muted">{peer.papers_imported} imported</span>
                      ) : null}
                    </td>
                    <td className="whitespace-nowrap">
                      {peer.last_pulled_at ? <Time iso={peer.last_pulled_at} withTime /> : "not yet"}
                    </td>
                    <td>{fingerprint(peer.owner_key_fingerprint)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>This platform does not follow any lab yet.</Empty>
        )}
      </Section>

      <Section id="trust" title="Trust levels">
        <ul className="space-y-2 text-sm">
          {Object.entries(TRUST).map(([key, meta]) => (
            <li key={key} className="flex flex-wrap items-baseline gap-2">
              <Chip tone={key === "full" ? "green" : key === "review" ? "blue" : "slate"}>{meta.text}</Chip>
              <span>
                {meta.description}
                {key === "full" && !data.full_trust_enabled ? (
                  <strong> Not enabled on this platform yet: it is being hardened against a compromised lab server.</strong>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-3 max-w-3xl text-sm text-muted">
          Whatever the trust, files are checked against their hashes and the platform&apos;s rules for submissions, and
          signatures against the keys the lab&apos;s owner attested. A lab publishes only what was submitted to it: if A
          follows B and B follows C, papers of C reach A only if A follows C. When a lab withdraws a paper or revokes a key,
          what depended on it is re-checked here.
        </p>
        <p className="mt-3 max-w-3xl text-sm text-muted">
          Each lab also has a scope: by default only its papers on problems in our catalog. The owner can choose areas
          instead (then also papers on problems we do not have) or all papers, and a minimum level the paper must reach at
          the lab. Papers outside the scope are only counted: nothing of them is downloaded, shown or checked here.
        </p>
      </Section>
    </div>
  );
}
