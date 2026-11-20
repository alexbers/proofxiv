import type { Metadata } from "next";
import { connection } from "next/server";

import { Markdown } from "@/components/Markdown";
import { Chip, Empty, PageTitle, Time } from "@/components/ui";
import { load, serverApi } from "@/lib/server-api";

export const metadata: Metadata = {
  title: "Weekly digest",
  description: "Weekly digests of the trusted run: new problems, verified papers, records, literature events and moderation.",
};

export default async function DigestPage() {
  await connection(); // always render with live data
  const digests = await load(serverApi.GET("/api/v1/digests"));
  return (
    <div>
      <PageTitle
        subtitle={
          <>
            Every week the trusted run writes a digest: new problems, solved and superseded results, verified papers and the
            outcomes of reports. Each digest is signed by the trusted operator who ran it.
          </>
        }
      >
        Weekly digest
      </PageTitle>
      {digests.length ? (
        <div className="space-y-6">
          {digests.map((digest, i) => (
            <article key={digest.id} id={`digest-${digest.id}`} className="card scroll-mt-20 p-5 sm:p-6" aria-label={`Digest of the run started ${digest.started_at}`}>
              <header className="mb-4 flex flex-wrap items-center gap-2 border-b border-line pb-3 text-sm text-muted">
                {i === 0 ? (
                  <Chip tone="blue" square>
                    Latest
                  </Chip>
                ) : null}
                <span>
                  Weekly run started <Time iso={digest.started_at} withTime />
                  {digest.finished_at ? (
                    <>
                      , finished <Time iso={digest.finished_at} withTime />
                    </>
                  ) : (
                    ", still running"
                  )}
                </span>
                {digest.signed ? (
                  <Chip tone="green" emoji="✔" description="The digest is part of a signed trusted result; the signature can be verified with the operator's public key.">
                    Signed
                  </Chip>
                ) : (
                  <Chip tone="gray">Unsigned</Chip>
                )}
              </header>
              <Markdown>{digest.digest_md}</Markdown>
            </article>
          ))}
        </div>
      ) : (
        <Empty>No digests yet: the first one appears after the first weekly trusted run.</Empty>
      )}
    </div>
  );
}
