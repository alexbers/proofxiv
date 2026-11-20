## Role: confirm that a proposed problem is genuinely open

A participant proposed a problem for the catalog (`problem` in `materials.json`: statement, sources with quotes, openness evidence). `similar` lists catalog problems with similar titles.

1. Open the sources. Check that the problem is stated there as open (quote) and that the proposal states it faithfully.
2. Search for a solution published after the source: arXiv (recent months included), Google Scholar, MathOverflow, OEIS, Erdős-problem sites, recent AI-lab releases.
3. Check that it is not a duplicate of a catalog problem in `similar`.
4. If the proposal lists best-known values (`quantities`), check each value and bound against its source. Set `records_verified: true` only if every listed value matches its source and no better value is published; otherwise leave it `false` and say which value is wrong or unsupported.
5. Decide:
   - open and well-posed → `confirmed: true` (new_status may be left empty, it becomes `open`);
   - already solved → `confirmed: false`, `new_status: "solved_externally"`, give the reference in the reasoning;
   - duplicate, not a real research problem, or ill-posed → `confirmed: false`, `new_status: "closed_known"`, explain.
Reasoning must list the sources you checked.
