You are looking for genuinely open mathematical problems to add to the ProofXiv catalog, so that AI agents can work on them.

`catalog.json` in the working directory lists the titles already in the catalog; do not propose those again.

Where to look: recent arXiv papers ("open problem", "we conjecture", "it remains open", final sections with open questions), OEIS conjectures, Erdős-problem sites, MathOverflow questions without accepted answers, survey tables with open entries (Ramsey numbers, codes, packings, designs), formal conjecture collections, benchmark lists of optimization constants.

For each candidate:
1. Quote the source that states the problem as open (`sources[].quote`) and give its URL.
2. Search for later solutions (recent arXiv included). Put what you searched and found into `openness_evidence`. Drop the candidate if it is solved.
3. State the problem precisely in `statement_md` (Markdown + LaTeX), with the definitions needed.
4. Add `domain`, a short `importance_note` and `difficulty_note`, `formalizable` (could a Lean statement be written?), and `quantities` for record-type problems (current best lower/upper bound with source).

Prefer problems where progress can be certified by a computer (explicit constructions, finite checks, Lean). Quality over quantity: propose at most the requested number. Your final answer is one JSON object matching the provided schema.
