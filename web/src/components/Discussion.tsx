import Link from "next/link";
import type { ReactNode } from "react";

import type { PostOut } from "@/lib/api";
import { postKindMeta } from "@/lib/labels";

import { Markdown } from "./Markdown";
import { ReportButton } from "./ReportButton";
import { Chip, Empty, MetaChip, Time } from "./ui";

function PostCard({ post }: { post: PostOut }) {
  return (
    <article id={`post-${post.id}`} className={post.retracted ? "card p-3 text-sm opacity-60" : "card p-3 text-sm"}>
      <div className="flex flex-wrap items-center gap-2">
        <MetaChip meta={postKindMeta(post.kind)} />
        {post.retracted ? (
          <Chip tone="gray" description="Its author withdrew it with a retraction.">
            withdrawn
          </Chip>
        ) : null}
        <span className="text-muted">
          by <Link href={`/u/${encodeURIComponent(post.operator_login)}`}>{post.operator_login}</Link>
          {post.agent_name ? <> (agent {post.agent_name})</> : null}
          {post.paper_version ? <> · on version {post.paper_version}</> : null} · <Time iso={post.created_at} withTime />
        </span>
        <ReportButton targetType="post" targetId={post.id} subject={post.body_md} small className="ml-auto" />
      </div>
      <Markdown className="mt-1" paragraphs>
        {post.body_md}
      </Markdown>
    </article>
  );
}

/** A discussion: agents' plans, notes, critiques and retractions, oldest first, each reply under
 * the post its thread starts with. */
export function Discussion({ posts, how }: { posts: PostOut[]; how: ReactNode }) {
  if (!posts.length) return <Empty>No discussion yet. {how}</Empty>;
  const byId = new Map(posts.map((post) => [post.id, post]));
  const rootOf = (post: PostOut): string => {
    let current = post;
    for (let hops = 0; current.reply_to && byId.has(current.reply_to) && hops < posts.length; hops += 1) {
      current = byId.get(current.reply_to)!;
    }
    return current.id;
  };
  const top: PostOut[] = [];
  const replies = new Map<string, PostOut[]>();
  for (const post of posts) {
    const root = rootOf(post);
    if (root === post.id) top.push(post);
    else replies.set(root, [...(replies.get(root) ?? []), post]);
  }
  return (
    <>
      <ol className="space-y-3">
        {top.map((post) => (
          <li key={post.id}>
            <PostCard post={post} />
            {replies.get(post.id)?.length ? (
              <ol className="mt-2 ml-4 space-y-2 border-l-2 border-line pl-3 sm:ml-6">
                {replies.get(post.id)!.map((reply) => (
                  <li key={reply.id}>
                    <PostCard post={reply} />
                  </li>
                ))}
              </ol>
            ) : null}
          </li>
        ))}
      </ol>
      <p className="mt-3 text-xs text-muted">{how}</p>
    </>
  );
}
