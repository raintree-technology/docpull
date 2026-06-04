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
        <p className="mb-4 font-mono text-sm text-muted-foreground">404</p>
        <h1 className="mb-3 text-2xl font-medium tracking-tight sm:text-3xl">
          Page not found
        </h1>
        <p className="mb-8 text-sm text-muted-foreground sm:text-base">
          The page you&apos;re looking for doesn&apos;t exist or has moved.
        </p>
        <Link
          href="/"
          className="min-h-11 inline-flex items-center rounded-xl bg-foreground px-4 py-2.5 text-sm font-medium text-background transition-opacity hover:opacity-90"
        >
          Back to home
        </Link>
      </div>
    </main>
  );
}
