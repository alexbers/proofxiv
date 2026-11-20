/** Sort orders of the paper list (values of the API's `sort`); the first one is the default. */
export const FEED_SORTS = [
  { value: "notable", label: "Notable" },
  { value: "new", label: "Newest" },
  { value: "verified", label: "Recently verified" },
  { value: "attention", label: "Most liked" },
] as const;

export type FeedSort = (typeof FEED_SORTS)[number]["value"];

export const DEFAULT_FEED_SORT: FeedSort = FEED_SORTS[0].value;

export function feedSort(value: string | undefined): FeedSort {
  return FEED_SORTS.find((s) => s.value === value)?.value ?? DEFAULT_FEED_SORT;
}
