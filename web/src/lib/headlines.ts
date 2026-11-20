/**
 * Headlines of notable results come from the server and are numeric results only:
 * "n(6,9) ≤ 4368 (previous best 7781)", the biggest relative step first.
 */

/** "n(6,9) ≤ 4368 (previous best 7781)" → ["n(6,9) ≤ 4368", "previous best 7781"] */
export function splitPrevious(headline: string): [string, string | null] {
  const m = headline.match(/^(.*\S)\s*\((previous best [^()]*)\)$/u);
  return m ? [m[1], m[2]] : [headline, null];
}
