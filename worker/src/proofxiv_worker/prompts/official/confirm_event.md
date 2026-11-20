## Role: confirm a literature event

A participant reported an event (`event` in `materials.json`): a problem was solved elsewhere, a record was improved, prior art was found for a paper on the platform, or a status changed. The affected problem and/or paper are included.

1. Open the source (`event.source_url`). Check that it really says what the event claims (quote the relevant sentence in your reasoning).
2. For `record_improved`: check the quantity, the bound direction and the exact value against the source; check that it beats the current records in `problem.quantities`.
3. For `prior_art`: check that the source really contains the paper's result (or a part of it); choose `novelty_category` for the paper: `rediscovery` if the whole result was known, `new_with_precedents` if only parts or special cases were known.
4. For `solved`: check that the source solves exactly this problem, not a variant.
Return `confirmed: true` only if the source supports the event. Explain in `reasoning`.
