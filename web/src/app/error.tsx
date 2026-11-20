"use client";

import Link from "next/link";
import { useEffect } from "react";

export default function ErrorPage({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  const digest = error instanceof Error && "digest" in error ? String((error as Error & { digest?: string }).digest) : null;
  return (
    <div className="mx-auto max-w-xl py-16 text-center" role="alert">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="mt-2 text-muted">
        The page could not be loaded. The ProofXiv API may be unavailable for a moment.
        {digest ? <span className="mt-1 block text-xs">Error id: {digest}</span> : null}
      </p>
      <p className="mt-6 flex flex-wrap justify-center gap-2">
        <button type="button" className="btn btn-primary" onClick={() => retry()}>
          Try again
        </button>
        <Link href="/" className="btn hover:no-underline">
          Go to the front page
        </Link>
      </p>
    </div>
  );
}
