import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-xl py-16 text-center">
      <p className="text-sm font-semibold tracking-wide text-muted uppercase">404</p>
      <h1 className="mt-2 text-2xl font-semibold">Not found</h1>
      <p className="mt-2 text-muted">
        This page does not exist, or the paper, problem or operator was not found.
      </p>
      <p className="mt-6 flex flex-wrap justify-center gap-2">
        <Link href="/" className="btn btn-primary hover:no-underline">
          Front page
        </Link>
        <Link href="/papers" className="btn hover:no-underline">
          Results
        </Link>
        <Link href="/problems" className="btn hover:no-underline">
          Problems
        </Link>
      </p>
    </div>
  );
}
