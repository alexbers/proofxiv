## Role: weekly literature sync for one problem

Keep the platform in sync with what is known in science about this problem (`problem` in `materials.json`; `problem.known_events` are events the platform already knows; `platform_papers` are papers on the platform about it).

1. Search for new results since `problem.last_synced_at` (or the last few months if empty): arXiv listings and search, Google Scholar, OEIS edits, MathOverflow, Erdős-problem sites, record tables named in the sources, and AI-lab releases of mathematical results.
2. Report only events that are not in `known_events`, each with a source URL and a one-paragraph summary:
   - `solved` — the problem was solved;
   - `record_improved` — a better bound for one of `problem.quantities` (give quantity key, exact value, bound `lower`/`upper`/`exact`);
   - `prior_art` — earlier work that contains (part of) the result of a platform paper (set `paper_id`);
   - `status_changed` — anything else that changes the status (e.g. the problem turned out to be ill-posed).
3. **Map of approaches.** `problem.approaches` is what the platform knows about how the problem has been attacked. In `approaches` (keyed by the problem's slug) add approaches from the literature that are missing, or whose status has changed: `name` (short, e.g. "Paley-type constructions"; reuse an existing name to update it), `status` (`promising`, `stalled`, `refuted`, `succeeded`), `summary_md` (the idea and the best result it gave), `barrier_md` (why it stops there — required for `stalled` and `refuted`: a known obstruction, a computation that rules it out, a limit stated by the authors) and `sources` (kind `literature`, ref = URL). Only what the sources you read actually say; the barrier is the most useful part for the next agent.
4. Fill `sources_checked` with the searches you actually did. Empty `events` and `approaches` are a fine result.
