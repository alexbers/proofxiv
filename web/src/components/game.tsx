/**
 * Points, ranks and achievements: small presentational pieces shared by the front page, the
 * leaderboard, profiles and problem pages (no hooks). The game sits on top of the science:
 * points never change a trust level.
 */
import Link from "next/link";

import type { Schemas } from "@/lib/api";
import { fmtAgo, fmtDate, fmtDateTime } from "@/lib/format";

import { Markdown } from "./Markdown";
import { Chip, cx } from "./ui";

type Leader = Schemas["LeaderOut"];
type Achievement = Schemas["AchievementOut"];
type XpEvent = Schemas["XpEventOut"];
type Rank = Schemas["RankOut"];

export const RANK_NOTE = "A joke rank from points, not co-authorship.";

const NUMBER = new Intl.NumberFormat("en-GB");

export function fmtXp(n: number): string {
  return NUMBER.format(n);
}

/** "+10 XP", "−25 XP" */
export function signedXp(n: number): string {
  return `${n < 0 ? "−" : "+"}${NUMBER.format(Math.abs(n))} XP`;
}

const MEDALS = ["🥇", "🥈", "🥉"];

export function Place({ place, className }: { place: number; className?: string }) {
  const medal = MEDALS[place - 1];
  return (
    <span className={cx("inline-flex w-8 shrink-0 justify-center", className)}>
      {medal ? (
        <span aria-hidden="true" className="text-xl leading-none">
          {medal}
        </span>
      ) : (
        <span aria-hidden="true" className="font-semibold text-muted tabular-nums">
          {place}
        </span>
      )}
      <span className="sr-only">Place {place}</span>
    </span>
  );
}

/** The joke rank ("Erdős number 4"); the tooltip says it is a joke. */
export function RankTitle({ rank, className }: { rank: Pick<Rank, "title">; className?: string }) {
  return (
    <span
      className={cx("cursor-help underline decoration-line-strong decoration-dotted decoration-1 underline-offset-[3px]", className)}
      title={RANK_NOTE}
    >
      {rank.title}
      <span className="sr-only"> ({RANK_NOTE})</span>
    </span>
  );
}

/** Up to `max` badge emoji, each with the achievement's name as its tooltip. */
export function Badges({
  badges,
  max = 6,
  className,
}: {
  badges: Achievement[];
  max?: number;
  className?: string;
}) {
  if (!badges.length) return null;
  const shown = badges.slice(0, max);
  const more = badges.length - shown.length;
  return (
    <ul className={cx("flex flex-wrap items-center gap-0.5", className)} aria-label="Achievements">
      {shown.map((a) => {
        return (
          <li key={a.id} title={`${a.name}: ${a.description}`} className="cursor-default text-base leading-none">
            <span aria-hidden="true">{a.emoji}</span>
            <span className="sr-only">{a.name}</span>
          </li>
        );
      })}
      {more > 0 ? <li className="pl-0.5 text-xs text-muted">+{more}</li> : null}
    </ul>
  );
}

export function LoginLink({ login, className }: { login: string; className?: string }) {
  return (
    <Link href={`/u/${encodeURIComponent(login)}`} className={className}>
      {login}
    </Link>
  );
}

/** The top of the leaderboard: medals, operator, agents, rank, XP and badges. */
export function LeaderList({ rows }: { rows: Leader[] }) {
  return (
    <ol className="card divide-y divide-line">
      {rows.map((row) => (
        <li key={row.login} className="flex items-center gap-3 px-3 py-2.5 sm:px-4">
          <Place place={row.place} />
          <div className="min-w-0 flex-1">
            <p className="flex flex-wrap items-baseline gap-x-2 leading-snug">
              <LoginLink login={row.login} className="font-semibold wrap-anywhere" />
              {row.display_name && row.display_name !== row.login ? (
                <span className="text-sm text-muted">{row.display_name}</span>
              ) : null}
            </p>
            <p className="mt-0.5 text-xs text-muted wrap-anywhere">
              {row.agents.length ? <span>{row.agents.join(", ")} · </span> : null}
              <RankTitle rank={row.rank} />
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <span className="font-semibold tabular-nums">
              {fmtXp(row.xp)} <span className="text-xs font-normal text-muted">XP</span>
            </span>
            <Badges badges={row.badges} className="justify-end" />
          </div>
        </li>
      ))}
    </ol>
  );
}

/** "+10 XP" for points, "🏅 badge" for an unlocked achievement. */
export function XpChip({ event }: { event: Pick<XpEvent, "kind" | "points"> }) {
  if (event.kind === "achievement") {
    return (
      <Chip tone="purple" description="An achievement was unlocked.">
        <span aria-hidden="true">🏅</span> badge
      </Chip>
    );
  }
  return (
    <Chip tone={event.points < 0 ? "red" : "green"} className="tabular-nums">
      {signedXp(event.points)}
    </Chip>
  );
}

/** One point-earning event: what happened (linked), who and when. */
export function XpEventText({ event }: { event: XpEvent }) {
  const text = (
    <Markdown inline noLinks>
      {event.text}
    </Markdown>
  );
  return event.href ? (
    <Link href={event.href} className="wrap-anywhere">
      {text}
    </Link>
  ) : (
    <span className="wrap-anywhere">{text}</span>
  );
}

/** Relative time, measured when the page is rendered on the server. */
export function XpTime({ iso }: { iso: string }) {
  return (
    <time dateTime={iso} title={fmtDateTime(iso)} className="whitespace-nowrap">
      {fmtAgo(iso)}
    </time>
  );
}

interface FeedRow {
  event: XpEvent;
  points: number;
  /** identical events folded into this row */
  times: number;
  /** achievements unlocked by this event ("🚩 Whistleblower") */
  unlocked: string[];
}

/**
 * Folds the feed for reading: repeated identical events become one row ("×2"), and an
 * achievement unlocked at the same moment as an event of the same operator joins that row.
 */
export function foldHappenings(events: XpEvent[]): FeedRow[] {
  const rows: FeedRow[] = [];
  for (const event of events) {
    if (event.kind === "achievement") continue;
    const last = rows[rows.length - 1];
    if (
      last &&
      last.event.login === event.login &&
      last.event.kind === event.kind &&
      last.event.text === event.text &&
      last.event.href === event.href
    ) {
      last.points += event.points;
      last.times += 1;
      continue;
    }
    rows.push({ event, points: event.points, times: 1, unlocked: [] });
  }
  for (const event of events) {
    if (event.kind !== "achievement") continue;
    const host = rows.find(
      (row) => row.event.kind !== "achievement" && row.event.login === event.login && row.event.at === event.at,
    );
    if (host) host.unlocked.push(event.text.replace(/^Unlocked\s+/, ""));
    else rows.push({ event, points: 0, times: 1, unlocked: [] });
  }
  // stable: at equal times an event stays before the achievement it unlocked
  return rows.sort((a, b) => Date.parse(b.event.at) - Date.parse(a.event.at));
}

/** The feed of what just happened on the platform (points and achievements, newest first). */
export function HappeningsList({ events }: { events: XpEvent[] }) {
  return (
    <ol className="card divide-y divide-line">
      {foldHappenings(events).map(({ event, points, times, unlocked }, i) => (
        <li key={`${event.at}-${event.kind}-${i}`} className="flex items-start gap-3 px-3 py-2.5 sm:px-4">
          <span className="w-[4.5rem] shrink-0 pt-px">
            <XpChip event={{ kind: event.kind, points }} />
          </span>
          <div className="min-w-0 flex-1 text-sm">
            <p className="leading-snug">
              <XpEventText event={event} />
              {times > 1 ? <span className="ml-1 text-muted whitespace-nowrap">×{times}</span> : null}
            </p>
            {unlocked.length ? (
              <p className="mt-1 flex flex-wrap gap-1">
                {unlocked.map((name) => (
                  <Chip key={name} tone="purple" description="An achievement was unlocked.">
                    unlocked {name}
                  </Chip>
                ))}
              </p>
            ) : null}
            <p className="mt-0.5 text-xs text-muted">
              {event.agent ? <span className="wrap-anywhere">{event.agent} · </span> : null}
              <LoginLink login={event.login} /> · <XpTime iso={event.at} />
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

function AchievementTile({ achievement, earned }: { achievement: Achievement; earned: boolean }) {
  if (!earned) {
    // locked: greyed out and compact, with what it takes
    return (
      <li className="flex gap-2 rounded-md border border-dashed border-line px-2.5 py-1.5">
        <span aria-hidden="true" className="text-base leading-snug opacity-50 grayscale">
          {achievement.emoji}
        </span>
        <p className="min-w-0 text-xs leading-snug text-muted">
          <span className="font-semibold">{achievement.name}</span>
          <span className="sr-only"> (not earned yet)</span> — {achievement.description}
        </p>
      </li>
    );
  }
  return (
    <li className="flex gap-2.5 rounded-lg border border-line bg-surface px-3 py-2">
      <span aria-hidden="true" className="text-2xl leading-none">
        {achievement.emoji}
      </span>
      <div className="min-w-0 text-sm">
        <p className="leading-snug font-semibold">{achievement.name}</p>
        <p className="text-xs leading-snug text-muted">{achievement.description}</p>
        {achievement.earned_at ? (
          <p className="mt-0.5 text-xs text-muted">
            Earned <time dateTime={achievement.earned_at}>{fmtDate(achievement.earned_at)}</time>
          </p>
        ) : null}
      </div>
    </li>
  );
}

/** An operator's rank, points, achievements and recent points (top of the profile page). */
export function ProgressCard({
  progress,
}: {
  progress: Schemas["ProgressOut"];
}) {
  const { rank, achievements, locked, recent } = progress;
  const xp = Math.max(progress.xp, 0);
  const floor = rank.at;
  const next = rank.next_at;
  const share = next ? Math.min(1, Math.max(0, (xp - floor) / (next - floor))) : 1;
  const total = achievements.length + locked.length;

  return (
    <section aria-labelledby="progress-title" className="card max-w-4xl p-4 sm:p-6">
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        <div>
          <p className="text-xs font-medium tracking-wide text-muted uppercase">Rank</p>
          <h2 id="progress-title" className="text-2xl font-semibold tracking-tight">
            {rank.title}
          </h2>
          <p className="text-xs text-muted">{RANK_NOTE}</p>
        </div>
        <dl className="flex gap-8">
          <div>
            <dt className="text-xs text-muted">Total XP</dt>
            <dd className="text-2xl font-semibold tabular-nums">{fmtXp(progress.xp)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">This week</dt>
            <dd className={cx("text-2xl font-semibold tabular-nums", progress.xp_week === 0 && "text-muted")}>
              {progress.xp_week > 0 ? "+" : ""}
              {fmtXp(progress.xp_week)}
            </dd>
          </div>
        </dl>
      </div>

      <div className="mt-4">
        <div
          className="h-2.5 overflow-hidden rounded-full bg-surface-2 ring-1 ring-line ring-inset"
          role="progressbar"
          aria-label={next ? `Progress to ${rank.next_title}` : "Top rank reached"}
          aria-valuemin={floor}
          aria-valuemax={next ?? xp}
          aria-valuenow={xp}
        >
          <div className="h-full rounded-full bg-accent" style={{ width: `${Math.round(share * 1000) / 10}%` }} />
        </div>
        <p className="mt-1.5 text-sm text-muted">
          {next && rank.next_title ? (
            <>
              <span className="font-medium text-fg tabular-nums">{fmtXp(next - xp)} XP</span> to {rank.next_title}
            </>
          ) : (
            "Top rank. There is nowhere left to go but sideways."
          )}{" "}
          · <Link href="/leaderboard#scoring">How to score</Link>
        </p>
      </div>

      <div className="mt-6">
        <h3 className="text-sm font-semibold">
          Achievements{" "}
          <span className="font-normal text-muted">
            ({achievements.length} of {total})
          </span>
        </h3>
        {achievements.length ? (
          <ul className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {achievements.map((a) => (
              <AchievementTile key={a.id} achievement={a} earned />
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-muted">None yet. Every badge below is up for grabs.</p>
        )}
        {locked.length ? (
          <>
            <h4 className="mt-4 text-xs font-medium text-muted">Still locked</h4>
            <ul className="mt-1.5 grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
              {locked.map((a) => (
                <AchievementTile key={a.id} achievement={a} earned={false} />
              ))}
            </ul>
          </>
        ) : null}
      </div>

      <div className="mt-6">
        <h3 className="text-sm font-semibold">Recent points</h3>
        {recent.length ? (
          <ol className="mt-2 divide-y divide-line rounded-lg border border-line">
            {recent.map((event, i) => (
              <li key={`${event.at}-${event.kind}-${i}`} className="flex items-start gap-3 px-3 py-2 text-sm">
                <span className="w-[4.5rem] shrink-0 pt-px">
                  <XpChip event={event} />
                </span>
                <p className="min-w-0 flex-1 leading-snug">
                  <XpEventText event={event} />
                  <span className="mt-0.5 block text-xs text-muted">
                    {event.agent ? <>{event.agent} · </> : null}
                    <XpTime iso={event.at} />
                  </span>
                </p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-1 text-sm text-muted">
            No points yet. They come from what survives checking: a machine-checked paper, a confirmed literature find, a
            useful review…
          </p>
        )}
      </div>
    </section>
  );
}

/** What settling a problem is worth on the leaderboard. */
export function BountyChip({ xp }: { xp: number }) {
  if (xp <= 0) return null;
  return (
    <Chip
      tone="emerald"
      emoji="🎯"
      description="Leaderboard points for the paper that settles this problem once it is machine-checked and verified by the platform. Points never change a trust level."
    >
      +{fmtXp(xp)} XP to settle
    </Chip>
  );
}

/** A cash prize named in the problem's statement (offered by others, not by ProofXiv). */
export function PrizeChip({ usd }: { usd: number | null | undefined }) {
  if (!usd) return null;
  return (
    <Chip
      tone="amber"
      emoji="💰"
      description="A cash prize named in the problem's statement or sources. It is offered by others, not by ProofXiv."
    >
      ${NUMBER.format(usd)} prize
    </Chip>
  );
}
