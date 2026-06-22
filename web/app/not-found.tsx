import type { Metadata } from "next";
import Link from "next/link";

// Spec: SEO / Soft 404s. This route renders under Next's not-found boundary,
// which returns a real HTTP 404 — never a 200 "looks empty" page. noindex keeps
// any stray 404 URLs out of the index.
export const metadata: Metadata = {
  title: "Page not found - docpull",
  robots: { index: false, follow: true },
};

export default function NotFound() {
  return (
    <main className="min-h-screen flex items-center justify-center px-6">
      <div className="max-w-md text-center">
        <p className="mb-4 font-mono text-sm font-medium text-muted-foreground">
          404
        </p>
        <h1 className="mb-3 text-2xl font-semibold leading-tight sm:text-3xl">
          Page not found
        </h1>
        <p className="mb-8 text-base leading-7 text-muted-foreground">
          The page you&apos;re looking for doesn&apos;t exist or has moved.
        </p>
        <Link
          href="/"
          className="inline-flex min-h-11 items-center rounded-lg bg-foreground px-4 py-2.5 text-[15px] font-semibold leading-5 text-background transition-opacity hover:opacity-90"
        >
          Back to home
        </Link>
      </div>
    </main>
  );
}
