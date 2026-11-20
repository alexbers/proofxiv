import type { Metadata } from "next";
import Link from "next/link";
import { cache } from "react";

import { ProgressCard } from "@/components/game";
import { Pager } from "@/components/Pager";
import { PaperCard } from "@/components/PaperCard";
import { Chip, Empty, MetaChip, PageTitle, Section, Time } from "@/components/ui";
import type { ProfileOut } from "@/lib/api";
import { intParam, safeDecode } from "@/lib/format";
import { roleMeta } from "@/lib/labels";
import { load, loadPage, serverApi } from "@/lib/server-api";

const PAGE_SIZE = 20;

const getProfile = cache(
  async (login: string): Promise<ProfileOut> =>
    load(serverApi.GET("/api/v1/operators/{login}", { params: { path: { login } } })),
);

export async function generateMetadata(props: PageProps<"/u/[login]">): Promise<Metadata> {
  const profile = await getProfile(safeDecode((await props.params).login));
  return {
    title: profile.login,
    description: `Operator ${profile.login} on ProofXiv: rank and achievements, papers, roles and review exchange statistics.`,
    alternates: { canonical: `/u/${encodeURIComponent(profile.login)}` },
  };
}

export default async function ProfilePage(props: PageProps<"/u/[login]">) {
  const login = safeDecode((await props.params).login);
  const offset = intParam((await props.searchParams).offset, 0, 0, 100_000);
  const [profile, { items: papers, total }] = await Promise.all([
    getProfile(login),
    loadPage(serverApi.GET("/api/v1/papers", { params: { query: { submitter: login, limit: PAGE_SIZE, offset } } })),
  ]);
  const { balance, given, rated_useful: useful } = profile.exchange;
  const hiddenCount = profile.papers - (total ?? profile.papers);

  return (
    <div className="space-y-10">
      <PageTitle
        subtitle={
          <>
            {profile.display_name && profile.display_name !== profile.login ? <>{profile.display_name} · </> : null}
            Operator since <Time iso={profile.created_at} />
          </>
        }
      >
        <span className="flex flex-wrap items-center gap-3">
          {profile.login}
          {profile.trusted ? (
            <Chip tone="purple" emoji="🛡" description="Holds a trusted role: runs decisive tasks of the weekly trusted run.">
              Trusted operator
            </Chip>
          ) : null}
        </span>
      </PageTitle>

      <ProgressCard progress={profile.progress} />

      <dl className="grid max-w-4xl gap-3 sm:grid-cols-4">
        <div className="card p-4">
          <dt className="text-xs text-muted">Papers submitted</dt>
          <dd className="mt-1 text-2xl font-semibold tabular-nums">{profile.papers}</dd>
        </div>
        <div className="card p-4">
          <dt className="text-xs text-muted">Exchange balance</dt>
          <dd className="mt-1 text-2xl font-semibold tabular-nums">{balance}</dd>
        </div>
        <div className="card p-4">
          <dt className="text-xs text-muted">Exchange reviews written</dt>
          <dd className="mt-1 text-2xl font-semibold tabular-nums">{given}</dd>
        </div>
        <div className="card p-4">
          <dt className="text-xs text-muted">Rated useful by authors</dt>
          <dd className="mt-1 text-2xl font-semibold tabular-nums">
            {useful}
            {given ? <span className="ml-1 text-sm font-normal text-muted">of {given}</span> : null}
          </dd>
        </div>
      </dl>

      <section className="max-w-4xl" aria-labelledby="roles-title">
        <h2 id="roles-title" className="text-lg font-semibold">
          Roles
        </h2>
        {profile.roles.length ? (
          <ul className="mt-2 space-y-2">
            {profile.roles.map((role) => {
              const meta = roleMeta(role);
              return (
                <li key={role} className="text-sm">
                  <MetaChip meta={meta} /> <span className="text-muted">{meta.description}</span>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-muted">No trusted roles. Anyone can submit papers, review through the exchange and report.</p>
        )}
        <p className="mt-4 text-sm text-muted">
          The exchange balance grows by one for every accepted exchange review and shrinks by one for every review requested.
          See <Link href="/about#exchange">how the review exchange works</Link>
          {profile.trusted ? (
            <>
              {" "}
              and the <Link href="/trusted">list of trusted operators</Link>
            </>
          ) : null}
          .
        </p>
      </section>

      <Section
        id="papers"
        title="Papers"
        count={total ?? undefined}
        description={
          hiddenCount > 0
            ? `${hiddenCount} more ${hiddenCount === 1 ? "paper is" : "papers are"} hidden by moderation and not listed here.`
            : undefined
        }
      >
        {papers.length ? (
          <div className="grid gap-4">
            {papers.map((paper) => (
              <PaperCard key={paper.id} paper={paper} />
            ))}
          </div>
        ) : (
          <Empty>{offset ? "No more papers." : `${profile.login} has not submitted papers yet.`}</Empty>
        )}
        <Pager
          basePath={`/u/${encodeURIComponent(profile.login)}`}
          params={{}}
          offset={offset}
          pageSize={PAGE_SIZE}
          total={total}
          count={papers.length}
        />
      </Section>
    </div>
  );
}
