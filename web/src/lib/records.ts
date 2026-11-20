/** Formatting of record values and of the size of a step ("+10 (+11%)", "−3,413 (−44%)", "×12,677"). */

const GROUPED = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

/** Parse "98", "2.2203344", "48/50" or "1e6" into a number (NaN if it is something else). */
export function parseValue(value: string): number {
  const text = value.trim().replace(/[\s,]/g, "");
  const fraction = /^(-?\d+(?:\.\d+)?)\/(\d+(?:\.\d+)?)$/.exec(text);
  if (fraction) return Number(fraction[1]) / Number(fraction[2]);
  return /^-?\d+(\.\d+)?(e-?\d+)?$/i.test(text) ? Number(text) : Number.NaN;
}

/** Large integers get thousands separators; anything else is shown as written. */
export function fmtValue(value: string): string {
  return /^-?\d{5,}$/.test(value.trim()) ? GROUPED.format(Number(value)) : value;
}

function fmtMagnitude(n: number): string {
  const abs = Math.abs(n);
  if (Number.isInteger(abs)) return GROUPED.format(abs);
  if (abs >= 100) return GROUPED.format(Math.round(abs));
  return String(Number(abs.toPrecision(3)));
}

/** 44, 4.8, 0.95, 0.011; null below 0.01 (shown as "<0.01%"). */
function fmtPercent(p: number): string | null {
  const abs = Math.abs(p);
  if (abs >= 10) return String(Math.round(abs));
  if (abs >= 1) return String(Number(abs.toFixed(1)));
  if (abs >= 0.01) return String(Number(abs.toPrecision(2)));
  return null;
}

/**
 * How big a step is: the change with its sign and, relative to the previous value, a percentage,
 * or the factor when a lower bound at least doubled. Null when it cannot be computed.
 */
export function changeText(bound: string, previous: string | null, value: string, gain?: number | null): string | null {
  if (previous === null) return null;
  const before = parseValue(previous);
  const after = parseValue(value);
  const diff = gain ?? after - before;
  if (!Number.isFinite(diff) || diff === 0) return null;
  const sign = diff > 0 ? "+" : "−";
  const step = `${sign}${fmtMagnitude(diff)}`;
  if (!Number.isFinite(before) || before === 0) return step;
  if (bound === "lower" && after / before >= 2) return `${step} (×${fmtMagnitude(after / before)})`;
  const percent = fmtPercent((diff / Math.abs(before)) * 100);
  return `${step} (${percent === null ? "<0.01" : `${sign}${percent}`}%)`;
}

/** The comparison sign of a bound: R ≥ 98, n ≤ 4368, x = 5. */
export function boundSign(bound: string): string {
  return bound === "lower" ? "≥" : bound === "upper" ? "≤" : "=";
}
