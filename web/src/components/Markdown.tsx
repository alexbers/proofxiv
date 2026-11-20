/**
 * Markdown with LaTeX math (remark-math + rehype-katex), GitHub tables and highlighted code.
 * Server Component only (code blocks are highlighted with Shiki on the server).
 * Raw HTML in the source is never rendered; unsafe link protocols are dropped by react-markdown.
 */
import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { remarkParagraphs } from "@/lib/remark-paragraphs";

import { CodeBlock } from "./CodeBlock";
import { cx } from "./ui";

interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

function textOf(node: HastNode | undefined): string {
  if (!node) return "";
  if (node.type === "text") return node.value ?? "";
  return (node.children ?? []).map(textOf).join("");
}

function languageOf(node: HastNode): string | undefined {
  const classes = node.properties?.className;
  if (!Array.isArray(classes)) return undefined;
  const lang = classes.map(String).find((c) => c.startsWith("language-"));
  return lang?.slice("language-".length);
}

function MdLink({ href, children, noLinks }: { href?: string; children?: ReactNode; noLinks?: boolean }) {
  if (noLinks || !href) return <span>{children}</span>;
  if (href.startsWith("/") || href.startsWith("#")) return <a href={href}>{children}</a>;
  if (/^https?:\/\//i.test(href)) {
    return (
      <a href={href} rel="noopener noreferrer nofollow ugc" target="_blank">
        {children}
      </a>
    );
  }
  return <a href={href} rel="nofollow ugc">{children}</a>;
}

function components(noLinks: boolean, inline: boolean): Components {
  const map: Components = {
    a: ({ href, children }) => (
      <MdLink href={href} noLinks={noLinks}>
        {children}
      </MdLink>
    ),
    // never load remote images from user content: show a link instead
    img: ({ src, alt }) =>
      noLinks || typeof src !== "string" ? (
        <span>[image{alt ? `: ${alt}` : ""}]</span>
      ) : (
        <MdLink href={src}>[image{alt ? `: ${alt}` : ""}]</MdLink>
      ),
    // user headings sit below the page's own h1/h2
    h1: ({ children }) => <h3>{children}</h3>,
    h2: ({ children }) => <h4>{children}</h4>,
    h3: ({ children }) => <h5>{children}</h5>,
    h4: ({ children }) => <h6>{children}</h6>,
    h5: ({ children }) => <h6>{children}</h6>,
    pre: ({ node, children }) => {
      const code = (node as HastNode | undefined)?.children?.find((c) => c.type === "element" && c.tagName === "code");
      if (code) return <CodeBlock code={textOf(code).replace(/\n$/, "")} lang={languageOf(code)} />;
      return <pre>{children}</pre>;
    },
  };
  // (a key with an `undefined` value would break rendering, so add it only when needed)
  if (inline) map.p = ({ children }) => <span className="md-p">{children} </span>;
  return map;
}

const KATEX_OPTIONS = { throwOnError: false, strict: "ignore" as const, trust: false };

export function Markdown({
  children,
  className,
  inline = false,
  noLinks = false,
  paragraphs = false,
}: {
  children: string | null | undefined;
  className?: string;
  /** render paragraphs as inline text (titles, short statements) */
  inline?: boolean;
  /** render links as plain text (e.g. inside another link) */
  noLinks?: boolean;
  /** keep single line breaks and split walls of text (fields of a structured review) */
  paragraphs?: boolean;
}) {
  if (!children) return null;
  const Tag = inline ? "span" : "div";
  return (
    <Tag className={cx("md", inline && "md-inline", className)}>
      <ReactMarkdown
        remarkPlugins={paragraphs ? [remarkGfm, remarkMath, remarkParagraphs] : [remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, KATEX_OPTIONS]]}
        components={components(noLinks, inline)}
      >
        {children}
      </ReactMarkdown>
    </Tag>
  );
}
