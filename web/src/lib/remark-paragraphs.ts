/**
 * A remark plugin for text written as a JSON string (fields of a structured review): models write
 * each field as one long line, or as lines separated by single newlines ("(a) …\n(b) …").
 */

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
}

const LONG_PARAGRAPH = 700;
const MIN_PARAGRAPH = 350;
const MIN_TAIL = 200;
// "Sec. 2", "e.g. The", "et al. Smith": a period that does not end a sentence
const ABBREVIATIONS = new Set(["e.g", "i.e", "al", "vs", "cf", "resp", "prop", "thm", "cor", "sec", "eq", "fig", "lem", "def", "rem", "no", "approx", "ref", "vol", "ch", "pp"]);
// the next sentence starts with a capital or a numbered item: "… prose. (1) lcmMatrix …"
const SENTENCE_END = /(\S+?)[.!?]["')\]]?\s+(?=[A-Z]|\(\w{1,4}\)\s)/g;

function lengthOf(node: MdNode): number {
  return (node.value ?? "").length + (node.children ?? []).reduce((sum, child) => sum + lengthOf(child), 0);
}

function endsSentence(word: string): boolean {
  const bare = word.replace(/^[("'[]+/, "").toLowerCase();
  return bare.length > 1 && !ABBREVIATIONS.has(bare);
}

/** Cut a long one-paragraph text into paragraphs at sentence ends (only in plain text runs, so
 * never inside code, math, links or emphasis). */
function splitParagraph(paragraph: MdNode): MdNode[] {
  const total = lengthOf(paragraph);
  if (total < LONG_PARAGRAPH) return [paragraph];
  const groups: MdNode[][] = [[]];
  let start = 0; // where the current paragraph starts, in characters of the whole text
  let pos = 0; // characters before the current child
  for (const child of paragraph.children ?? []) {
    const value = child.value ?? "";
    if (child.type !== "text") {
      groups[groups.length - 1].push(child);
      pos += lengthOf(child);
      continue;
    }
    let from = 0;
    for (const match of value.matchAll(SENTENCE_END)) {
      const end = match.index + match[0].trimEnd().length;
      const next = match.index + match[0].length;
      if (pos + end - start < MIN_PARAGRAPH || total - (pos + next) < MIN_TAIL || !endsSentence(match[1])) continue;
      groups[groups.length - 1].push({ type: "text", value: value.slice(from, end) });
      groups.push([]);
      from = next;
      start = pos + next;
    }
    groups[groups.length - 1].push({ type: "text", value: value.slice(from) });
    pos += value.length;
  }
  return groups.map((children) => ({ type: "paragraph", children }));
}

/** Text written as a JSON string (review fields): keep the writer's single line breaks ("(a) …\n(b) …")
 * and break walls of text into paragraphs. */
export function remarkParagraphs() {
  // Only nodes that have children get them rewritten: a leaf with a `children` key (even undefined),
  // such as inline math, is taken for an element by mdast-util-to-hast and breaks the page.
  const keepBreaks = (node: MdNode) => {
    if (!node.children) return;
    node.children = node.children.flatMap((child) => {
      if (child.type !== "text" || !child.value?.includes("\n")) {
        keepBreaks(child);
        return [child];
      }
      return child.value.split("\n").flatMap((line, i) => [...(i ? [{ type: "break" }] : []), { type: "text", value: line }]);
    });
  };
  return (tree: MdNode) => {
    keepBreaks(tree);
    if (!tree.children) return;
    tree.children = tree.children.flatMap((child) =>
      child.type === "paragraph" && !child.children?.some((c) => c.type === "break") ? splitParagraph(child) : [child],
    );
  };
}
